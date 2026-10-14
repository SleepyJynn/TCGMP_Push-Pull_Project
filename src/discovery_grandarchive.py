"""
Grand Archive TCG (Weebs of the Shore) sealed-product discovery script.

Scope (per the intern project brief): ALL official sealed products from
GA's own site, no card-content filtering, no duplicates. Draft Packs are
excluded per explicit confirmation -- every Draft Pack sheet says outright
"for event use only and are not intended for sale", same precedent as the
GA tracker's existing exclusions (Event/Draft/Supporter Packs, Demo Decks).

Data source: gatcg.com has no structured per-product HTML page (confirmed
live). The real data -- SKU, UPC, MSRP, Contents, Box Configuration,
Rarity Distribution, Release Date -- lives in linked PDF "Product Sheet"
files on GA's own S3/CDN. Getting clean text out of these took 5 rounds of
diagnostics against REAL output (never guessed from how a PDF "should"
look):
  1. First raw dump: found PowerShell `>` redirection mangling characters
     and some lines rendering with every letter doubled.
  2. Tried pdfplumber's dedupe_chars() + a guessed character-fix map for
     the "mojibake" -- round-trip showed BOTH guesses were wrong.
  3. Pulled real codepoints + real character coordinates (not display) --
     found the "mojibake" was never bad data (correct Unicode the whole
     time, PowerShell's `>` was just re-encoding it on write), and found
     the doubling is REAL duplicate text baked into the PDF at near-
     identical coordinates (confirmed: 'm' in "meant" appears twice at
     x0=35.04, top=336.20, exactly).
  4. Built a manual position-based dedup (pdfplumber's own dedupe_chars()
     wasn't catching it) + had the script write its own UTF-8 file
     directly instead of going through `>` + scanned for the one real
     encoding issue: Private Use Area ligature glyphs. Found U+E03C ("tt"
     ligature) with hard evidence, but one PDF (Mortal Ambition Alter
     Edition) was still doubled -- its duplicate sits at a different
     offset than the others.
  5. Widened the dedup match from exact-position to within 2pt, and
     confirmed via another real run all three ligatures (U+E03C "tt",
     U+E039 "ft", U+E048 "Qu") and a fully clean dump across all 11 sets.

Three sets/editions have NO usable data and are flagged, not fabricated:
  - Dawn of Ashes: its only linked PDF has no structured fields at all
    (just cover art, title, release date) on any of its 3 pages.
  - Distorted Reflections Alter Edition: PDF has zero extractable text on
    any of its 3 pages (no text layer -- likely image/vector-only).
  - .asphodel/cantabile: same, zero extractable text on all 8 pages.
These need manual research; this script will not guess values for them.

THIRD-PARTY FALLBACK (supervisor-approved when official data is unavailable):
KNOWN_UNPULLABLE lists products the official sheets cannot supply. When the
search term matches one of them (or blank = ALL), or an official search finds
nothing, the pull is redirected to retailer Shopify catalogs (THIRD_PARTY_STORES).
Third-party rows are ALWAYS printed in a separate section labelled NOT OFFICIAL
with their source URL; official data always wins (a retailer row whose barcode
or SKU matches an official product is dropped). Nothing is invented: a gap with
no retailer match is reported as still unresolved.

Usage:
    python discovery_grandarchive.py [--out=<path>]
    Prompts for a product name (or SKU) to filter by; press Enter for ALL
    products. No language prompt -- Grand Archive is English-only, unlike
    Digimon/One Piece's EN/JP split.

Requires: pip install requests pdfplumber --break-system-packages
"""

import sys
import io
import re
import time
import argparse
import json

try:
    import requests
    import pdfplumber
except ImportError as e:
    print(f"Missing dependency: {e}. Run: pip install requests pdfplumber --break-system-packages")
    sys.exit(1)

BASE = "https://www.gatcg.com"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
})

# Confirmed live (5 Oct 2026) via https://www.gatcg.com/article/stocking-products
# -- the 11 official sets and their exact Product Information article URLs.
KNOWN_SETS = [
    ("Dawn of Ashes", f"{BASE}/article/dawn-of-ashes-products"),
    ("Fractured Crown", f"{BASE}/article/fractured-crown-product-information"),
    ("Alchemical Revolution", f"{BASE}/article/alchemical-revolution-product-information"),
    ("Mercurial Heart", f"{BASE}/article/mercurial-heart-product-information"),
    ("Mortal Ambition", f"{BASE}/article/mortal-ambition-product-information"),
    ("Abyssal Heaven", f"{BASE}/article/abyssal-heaven-product-information"),
    ("Distorted Reflections", f"{BASE}/article/distorted-reflections-product-information"),
    ("Phantom Monarchs", f"{BASE}/article/phantom-monarchs-product-information"),
    ("Radiant Origins", f"{BASE}/article/radiant-origins-product-information"),
    (".asphodel/paradise", f"{BASE}/article/PRD-product-information"),
    (".asphodel/cantabile", f"{BASE}/article/asphodel-cantabile-product-information"),
]

# Confirmed dead ends (real runs, not assumed) -- flagged for the user
# rather than silently skipped or guessed.
KNOWN_NO_DATA = {
    "Dawn of Ashes": "Linked PDF has no structured product fields on any page (cover/release-date only).",
    "Distorted Reflections": None,  # handled per-PDF below (its 1E sheet IS fine; only the AE sheet is empty)
    ".asphodel/cantabile": "Linked PDF has zero extractable text on any of its 8 pages (likely no text layer).",
}

# Products the official sheets cannot supply. Built from the live runs
# (13 products parsed, 6 PDFs flagged) plus the GA release tracker. Entries
# are set/product level; "match" = phrases that must ALL appear in a retailer
# title (normalised: lowercase, punctuation -> space). Over-matching is safe
# because any retailer row duplicating an official product is dropped
# (barcode/SKU), and the entry list is NOT verified product-by-product --
# edit it freely as gaps are confirmed or closed. Optional "exclude" / "any_of"
# phrase lists narrow an entry (e.g. only the Alter Edition of a set), because
# the first live run showed retailer rows that were already-official 1E
# products being listed under an Alter Edition gap.
KNOWN_UNPULLABLE = [
    {"set": "Dawn of Ashes", "label": "Dawn of Ashes (all products)", "match": ["dawn of ashes"],
     "reason": "Official sheet has cover art only, no product fields."},
    {"set": "Alchemical Revolution", "label": "Alchemical Revolution (1E booster + starter decks)", "match": ["alchemical revolution"], "exclude": ["alter edition", "alter"],
     "reason": "Base sheet is character art only; the Alter Edition booster IS official (excluded here)."},
    {"set": "Mercurial Heart", "label": "Mercurial Heart (boosters + decks)", "match": ["mercurial heart"],
     "reason": "Both official sheets are cover/art only."},
    {"set": "Mercurial Heart", "label": "Silvie Re:Collection deck", "match": ["silvie", "re collection"], "exclude": ["lite"],
     "reason": "Mercurial Heart sheets have no product fields."},
    {"set": "Mercurial Heart", "label": "Tristan Re:Collection deck", "match": ["tristan", "re collection"], "exclude": ["lite"],
     "reason": "Mercurial Heart sheets have no product fields."},
    # The "Lite" versions are a SEPARATE, later product (retailer SKUs
    # GA25REL1/GA25REL2), not the 2024 Re:Collection boxes. Release date:
    # GA's own X post "Silvie Re:Collection Lite ... and Tristan Re:Collection
    # Lite ... are available now!" -- post ID 1928539299877691700 decodes to
    # 30 May 2025 (same day as the Mercurial Heart Alter Edition).
    {"set": "Mercurial Heart", "label": "Silvie Re:Collection Lite deck", "match": ["silvie", "lite"],
     "release_date": "30 May 2025", "release_source": "https://x.com/grandarchivetcg/status/1928539299877691700",
     "reason": "Not on any official product sheet; 2025 reprint-style 'Lite' deck."},
    {"set": "Mercurial Heart", "label": "Tristan Re:Collection Lite deck", "match": ["tristan", "lite"],
     "release_date": "30 May 2025", "release_source": "https://x.com/grandarchivetcg/status/1928539299877691700",
     "reason": "Not on any official product sheet; 2025 reprint-style 'Lite' deck."},
    {"set": "Distorted Reflections", "label": "Distorted Reflections Alter Edition", "match": ["distorted reflections", "alter"],
     "reason": "Alter Edition PDF has no text layer (1E products + starters are official)."},
    {"set": ".asphodel/cantabile", "label": ".asphodel/cantabile (all products)", "match": ["asphodel", "cantabile"],
     "reason": "Official PDF has no text layer."},
    {"set": ".asphodel/paradise", "label": ".asphodel/paradise (SKU/UPC/MSRP + starter decks)", "match": ["asphodel", "paradise"],
     "reason": "Booster parsed but source lists no SKU/UPC/MSRP; starter decks not on sheet."},
    {"set": "Abyssal Heaven", "label": "Guo Jia Re:Collection deck", "match": ["guo jia"],
     "product_urls": ["https://store.wizardtower.com/products/grand-archive-re-collection-guo-jia-heavens-favored"],
     "reason": "Named on a data-less page of the official sheet."},
    {"set": "Abyssal Heaven", "label": "Idyll Corsage Re:Collection deck", "match": ["idyll corsage"],
     "reason": "Official API lists a 'Re:Collection Idyll Corsage' set (7 Mar 2025) but the Abyssal Heaven sheet has no product data; not yet confirmed as a separate sealed product."},
    {"set": "Phantom Monarchs", "label": "Merlin Re:Collection deck", "match": ["merlin", "re collection"],
     "product_urls": ["https://contrariangaming.com/products/grand-archive-tcg-merlin-brilliant-vestige-re-collection",
                      "https://otakumart.co.nz/products/merlin-re-collection-brilliant-vestige-grand-archive-tcg"],
     "reason": "Named on a data-less page of the official sheet."},
    {"set": "Radiant Origins", "label": "Mordred Re:Collection deck", "match": ["mordred", "re collection"],
     "product_urls": ["https://flipsidegaming.com/products/grand-archive-re-collection-mordred-aurelian-regent"],
     "reason": "Named on a data-less page of the official sheet."},
    {"set": "Radiant Origins", "label": "Pantheon Decks (Lorraine/Zander/Arisanna/Kongming)", "match": ["radiant origins", "pantheon"],
     "reason": "Named on a data-less page of the official sheet."},
]

# UNCONFIRMED until ga_thirdparty_diag.py has been run: these are the
# standard Shopify public catalog URLs; a failure is reported, not hidden.
THIRD_PARTY_STORES = [
    ("flipsidegaming.com", "https://flipsidegaming.com/collections/grand-archive-sealed/products.json"),
    ("stompinggroundstcg.com", "https://www.stompinggroundstcg.com/collections/grand-archive/products.json"),
]
# Entry "product_urls" (found via web search, UNCONFIRMED) are fetched as
# <url>.json -- Shopify's standard single-product endpoint -- only for gaps the
# catalogs above could not fill. Any failure is printed, never hidden.
_SEALED_WORDS = ("booster", "starter", "deck", "box", "case", "display", "pack", "bundle")

_PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf[^"]*)"', re.IGNORECASE)
_OG_IMAGE_RE = re.compile(r'<meta[^>]+(?:property|name)="og:image"[^>]+content="([^"]+)"'
                          r'|<meta[^>]+content="([^"]+)"[^>]+(?:property|name)="og:image"', re.IGNORECASE)
# set name -> the set's official cover image (og:image of its Product
# Information article). Confirmed 13 Oct 2026 on the Phantom Monarchs page:
# the ONLY image on these pages is one set-level cover
# (PTM_Product_Information_Cover_....jpg); GA publishes no per-product image
# URLs. Product photos live inside the PDFs (no URL), so a per-product image
# comes from a retailer listing matched by UPC instead (see attach_images).
SET_COVERS = {}
# set page URL -> release date stated in the article text, e.g. the
# .asphodel/cantabile page: ".asphodel/cantabile releases on December 11th,
# 2026!" (confirmed 14 Oct 2026). Used for gap sets whose PDF is unreadable.
SET_PAGE_RELEASE = {}
_PAGE_RELEASE_RE = re.compile(r'releas(?:es|ing|ed)\s+on\s+([A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})')

_LIGATURE_FIXES = {
    "": "tt",
    "": "ft",
    "": "Qu",
}


def fix_ligatures(text):
    for bad, good in _LIGATURE_FIXES.items():
        text = text.replace(bad, good)
    return text


def fetch_pdf_links(info_url):
    resp = session.get(info_url, timeout=20)
    resp.raise_for_status()
    og = _OG_IMAGE_RE.search(resp.text)
    if og:
        SET_COVERS[info_url] = og.group(1) or og.group(2)
    rel = _PAGE_RELEASE_RE.search(re.sub(r"<[^>]+>", " ", resp.text))
    if rel:
        SET_PAGE_RELEASE[info_url] = fmt_release_date(re.sub(r"\s+", " ", rel.group(1)))
    links = _PDF_HREF_RE.findall(resp.text)
    seen = set()
    out = []
    for link in links:
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out


def fetch_pdf_bytes(pdf_url, referer, max_attempts=3):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.get(pdf_url, timeout=30, headers={"Referer": referer})
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            last_err = e
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    raise last_err


def dedupe_page_chars(page, tolerance=2.0):
    """Confirmed necessary via live diagnostics: these PDFs store some
    phrases twice at near-identical coordinates (bold-simulation artifact).
    pdfplumber's own dedupe_chars() did not catch all of them, so this
    does the dedup manually against page.chars."""
    kept = []
    keep_ids = set()
    for c in page.chars:
        text, x0, top = c["text"], c["x0"], c["top"]
        is_dupe = False
        for kt, kx0, ktop in kept:
            if kt == text and abs(kx0 - x0) <= tolerance and abs(ktop - top) <= tolerance:
                is_dupe = True
                break
        if is_dupe:
            continue
        kept.append((text, x0, top))
        keep_ids.add(id(c))

    def _keep(obj):
        if obj.get("object_type") != "char":
            return True
        return id(obj) in keep_ids

    return page.filter(_keep)


def fetch_pdf_pages_text(pdf_url, referer):
    """Returns a list of per-page cleaned text (deduped + ligature-fixed)."""
    content = fetch_pdf_bytes(pdf_url, referer)
    pages = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            deduped = dedupe_page_chars(page)
            text = deduped.extract_text() or ""
            if text.strip():
                text = fix_ligatures(text)
            pages.append(text)
    return pages


# ---------------------------------------------------------------------
# Field extraction. These PDF pages do NOT read in a consistent linear
# order (confirmed via live dumps -- e.g. "MSRP [Pack]" can appear before
# "Product Info" and before "SKU:"), so every field is searched for
# independently across the whole page text rather than parsed as one
# sequential pass (which is what worked for OP/Digimon's clean HTML, but
# does not apply here).
# ---------------------------------------------------------------------

_NAME_BOOSTER_CODED_RE = re.compile(r'^(.+?)\s*\(([A-Z0-9]+)\)\s*(Booster Pack|Draft Pack)\s*$', re.MULTILINE)
# Fallback for sets whose heading has no parenthetical code at all, e.g.
# "Fractured Crown Booster Pack" (confirmed via real dump -- not every set
# includes a product code in this heading line the way Radiant Origins
# "(RDO1E) Booster Pack" or Mortal Ambition "(AMB1E) Booster Pack" do).
_NAME_BOOSTER_PLAIN_RE = re.compile(r'^(.+?)\s+(Booster Pack|Draft Pack)\s*$', re.MULTILINE)
_NAME_STARTER_RE = re.compile(r'^(.+?)\s+Starter Deck\s*$', re.MULTILINE)
_SKU_RE = re.compile(r'SKU:\s*(\S+)')
_UPC_RE = re.compile(r'[•*]\s*(.+?)\s*\[(\d{10,14})\]')
_MSRP_RE = re.compile(r'MSRP\s*\[(\w+)\]\s*([\d.]+)\s*USD(?:\s*\(([\d.]+)\s*USD MAP\))?')
_UNIQUE_CARDS_RE = re.compile(r'(?:Over\s*)?(\d+)\s*unique cards in set')
_FOIL_VAR_RE = re.compile(r'(Over\s*)?(\d+)\s*foil variations in set')
_PER_PACK_RE = re.compile(r'(\d+)\s*randomly inserted cards per pack')
_PACKS_PER_BOX_RE = re.compile(r'(\d+)\s*booster packs per booster box')
_BOXES_PER_CASE_RE = re.compile(r'(\d+)\s*booster boxes per case')
_DECKS_PER_DISPLAY_RE = re.compile(r'(\d+)\s*starter decks per display')
_DISPLAYS_PER_CASE_RE = re.compile(r'(\d+)\s*displays per case')
_RARITY_DIST_RE = re.compile(
    r'(\d+C\s*/\s*\d+U\s*/\s*\d+R\s*/\s*\d+SR(?:\s*/\s*\d+UR)?(?:\s*/\s*\d+CSR)?(?:\s*/\s*\d+CUR)?)'
)
_DIMENSIONS_RE = re.compile(r'Dimensions \(Box\):\s*(.+)')
_WEIGHT_RE = re.compile(r'Weight \(Box\):\s*(.+)')
_RELEASE_RE = re.compile(r'Releasing\s+([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})')
_NOT_FOR_SALE_RE = re.compile(r'are for event use only and are not intended for sale', re.IGNORECASE)


def fmt_release_date(raw):
    """'August 25th, 2023' -> '25 Aug 2023' (DD Mon YYYY); raw text if unparseable."""
    from datetime import datetime
    try:
        return datetime.strptime(re.sub(r"(\d)(st|nd|rd|th)", r"\1", raw).replace(",", ""), "%B %d %Y").strftime("%d %b %Y")
    except Exception:
        return raw


def parse_product_page(text, set_name, edition_hint):
    """Returns a dict of extracted fields for one product page, or None if
    this page carries no product signal at all (cover pages, lore pages,
    reprint lists, etc.) -- or if it IS a product but a Draft Pack, which
    is excluded per the confirmed not-for-sale precedent."""
    product_type = None
    product_name = None
    product_code = None
    coded_match = _NAME_BOOSTER_CODED_RE.search(text)
    if coded_match:
        product_name, product_code, product_type = coded_match.groups()
        product_name = product_name.strip()
    else:
        plain_match = _NAME_BOOSTER_PLAIN_RE.search(text)
        if plain_match:
            product_name, product_type = plain_match.groups()
            product_name = product_name.strip()
        else:
            starter_match = _NAME_STARTER_RE.search(text)
            if starter_match:
                product_name = starter_match.group(1).strip() + " Starter Deck"
                product_type = "Starter Deck"

    # Exclude Draft Packs by their OWN heading only -- confirmed bug: a
    # real Booster Pack page can mention "8 draft packs per case" in
    # passing (e.g. Mortal Ambition Alter Edition), which a page-wide
    # keyword search would wrongly treat as a Draft Pack page and exclude
    # entirely. Only exclude when the page's own product IS a Draft Pack.
    if product_type == "Draft Pack" or _NOT_FOR_SALE_RE.search(text):
        return None

    sku_match = _SKU_RE.search(text)
    sku = sku_match.group(1) if sku_match else None

    # A page counts as a real product if it has its own heading (Booster
    # Pack / Starter Deck) OR a SKU -- not SKU alone. Confirmed via a live
    # run: .asphodel/paradise's booster pack (254 unique cards, full
    # rarity distribution, box config) has NO SKU/UPC in the source PDF at
    # all (it's an unreleased set), and requiring SKU discarded all of
    # that real data. Requiring EITHER signal keeps that data while still
    # correctly rejecting cover/character-art pages, which have neither.
    if not product_name and not sku:
        return None

    upcs = _UPC_RE.findall(text)  # list of (label, code)

    msrps = []
    for unit, price, map_price in _MSRP_RE.findall(text):
        entry = f"{price} USD"
        if map_price:
            entry += f" ({map_price} USD MAP)"
        msrps.append((unit, entry))

    unique_cards = _UNIQUE_CARDS_RE.search(text)
    foil_match = _FOIL_VAR_RE.search(text)
    foil_variations = None
    if foil_match:
        foil_variations = f"Over {foil_match.group(2)}" if foil_match.group(1) else foil_match.group(2)

    per_pack = _PER_PACK_RE.search(text)
    packs_per_box = _PACKS_PER_BOX_RE.search(text)
    boxes_per_case = _BOXES_PER_CASE_RE.search(text)
    decks_per_display = _DECKS_PER_DISPLAY_RE.search(text)
    displays_per_case = _DISPLAYS_PER_CASE_RE.search(text)
    rarity = _RARITY_DIST_RE.search(text)
    dimensions = _DIMENSIONS_RE.search(text)
    weight = _WEIGHT_RE.search(text)
    release = _RELEASE_RE.search(text)

    edition = "Alter Edition" if "Alter Edition" in text else ("First Edition" if "First Edition" in text else edition_hint)

    return {
        "set_name": set_name,
        "product_name": product_name,
        "product_code": product_code,
        "product_type": product_type,
        "edition": edition,
        "sku": sku,
        "release_date": fmt_release_date(release.group(1)) if release else None,
        "msrps": msrps,
        "upcs": upcs,
        "unique_cards": unique_cards.group(1) if unique_cards else None,
        "foil_variations": foil_variations,
        "cards_per_pack": per_pack.group(1) if per_pack else None,
        "packs_per_box": packs_per_box.group(1) if packs_per_box else None,
        "boxes_per_case": boxes_per_case.group(1) if boxes_per_case else None,
        "decks_per_display": decks_per_display.group(1) if decks_per_display else None,
        "displays_per_case": displays_per_case.group(1) if displays_per_case else None,
        "rarity_distribution": rarity.group(1) if rarity else None,
        "dimensions_box": dimensions.group(1).strip() if dimensions else None,
        "weight_box": weight.group(1).strip() if weight else None,
    }


def dedupe_products(products):
    """No-duplicates requirement: de-dupe by (SKU) primarily, falling back
    to (set_name, product_name, edition) if SKU is missing."""
    seen = set()
    out = []
    for p in products:
        key = p["sku"] or (p["set_name"], p["product_name"], p["edition"])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def format_product(p, out):
    out(f"\n{p['set_name']} -- {p['product_name']} ({p['edition'] or 'Edition unknown'})")
    if p["product_code"]:
        out(f"  Product code: {p['product_code']}")
    if p["sku"]:
        out(f"  SKU: {p['sku']}")
    else:
        out("  SKU: (not listed in source PDF)")
    if p["release_date"]:
        out(f"  Release date: {p['release_date']}")
    if p.get("image"):
        out(f"  Image: {p['image']}")
        out(f"    (retailer photo from {p['image_source']}, matched by {p['image_match']} -- GA publishes no product image URLs)")
    else:
        out("  Image: (no retailer listing with a matching UPC/SKU)")
    if p.get("set_cover"):
        out(f"  Set cover image (official, whole set -- not this product): {p['set_cover']}")
    for unit, price in p["msrps"]:
        out(f"  MSRP [{unit}]: {price}")
    if p["unique_cards"]:
        out(f"  Unique cards in set: {p['unique_cards']}")
    if p["foil_variations"]:
        out(f"  Foil variations in set: {p['foil_variations']}")
    if p["cards_per_pack"]:
        out(f"  Randomly inserted cards per pack: {p['cards_per_pack']}")
    if p["packs_per_box"]:
        out(f"  Booster packs per booster box: {p['packs_per_box']}")
    if p["boxes_per_case"]:
        out(f"  Booster boxes per case: {p['boxes_per_case']}")
    if p["decks_per_display"]:
        out(f"  Starter decks per display: {p['decks_per_display']}")
    if p["displays_per_case"]:
        out(f"  Displays per case: {p['displays_per_case']}")
    if p["rarity_distribution"]:
        out(f"  Rarity distribution: {p['rarity_distribution']}")
    if p["dimensions_box"]:
        out(f"  Dimensions (Box): {p['dimensions_box']}")
    if p["weight_box"]:
        out(f"  Weight (Box): {p['weight_box']}")
    # UPCs are not printed (not needed for the migration, per instruction
    # 14 Oct 2026) -- they are still read, to match retailer images.


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def fetch_store_catalog(name, url, max_pages=10):
    """Returns (variant_records, error_or_None) for one Shopify store."""
    records = []
    for page in range(1, max_pages + 1):
        try:
            resp = session.get(url, params={"limit": 250, "page": page},
                               headers={"Accept": "application/json"}, timeout=30)
        except Exception as e:
            return records, f"{type(e).__name__}: {e}"
        if resp.status_code != 200:
            if page == 1:
                return records, f"HTTP {resp.status_code}"
            break
        try:
            products = resp.json().get("products", [])
        except Exception as e:
            return records, f"Not JSON ({e})"
        if not products:
            break
        base = url.split("/collections/")[0]
        for p in products:
            imgs = {i.get("id"): i.get("src") for i in p.get("images") or []}
            first_img = (p.get("images") or [{}])[0].get("src")
            for v in p.get("variants", []):
                title = p.get("title") or ""
                vt = v.get("title")
                if vt and vt != "Default Title":
                    title = f"{title} ({vt})"
                records.append({
                    "store": name, "title": title,
                    "url": f"{base}/products/{p.get('handle')}",
                    "vendor": p.get("vendor"), "product_type": p.get("product_type"),
                    "published_at": p.get("published_at"),
                    "sku": (v.get("sku") or "").strip() or None,
                    "barcode": (v.get("barcode") or "").strip() or None,
                    "price": v.get("price"),
                    "image": imgs.get(v.get("image_id")) or first_img,
                })
        if len(products) < 250:
            break
    return records, None


def fetch_product_json(url):
    """One Shopify product page as variant records via <url>.json."""
    from urllib.parse import urlparse
    try:
        resp = session.get(url.rstrip("/") + ".json", headers={"Accept": "application/json"}, timeout=30)
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}"
        p = resp.json().get("product") or {}
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    host = urlparse(url).netloc.replace("www.", "")
    recs = []
    first_img = (p.get("images") or [{}])[0].get("src")
    for v in p.get("variants", []):
        title = p.get("title") or ""
        if v.get("title") and v.get("title") != "Default Title":
            title = f"{title} ({v.get('title')})"
        recs.append({"store": host, "title": title, "url": url, "vendor": p.get("vendor"),
                     "product_type": p.get("product_type"), "published_at": p.get("published_at"),
                     "sku": (v.get("sku") or "").strip() or None,
                     "barcode": (v.get("barcode") or "").strip() or None, "price": v.get("price"),
                     "image": first_img})
    return recs, None


def is_sealed_candidate(title):
    t = norm(title)
    if "draft pack" in t:  # excluded from scope, same as official side
        return False
    if " re collection " in " " + t + " ":  # deck line, titles often lack 'deck'
        return True
    return any(w in t.split() or w + "s" in t.split() for w in _SEALED_WORDS)


def entry_matches_query(entry, qn):
    hay = norm(entry["set"] + " " + entry["label"])
    return qn in hay or norm(entry["set"]) in qn


def norm_sku(sku):
    """Make retailer SKUs comparable across stores and with official SKUs:
    'WOSGA26B10' and 'GA26B10-EN' -> 'ga26b10'; case SKUs ('WOSGA26caseB10')
    collapse onto their box ('ga26b10'). Confirmed patterns from the real run."""
    t = re.sub(r"[^a-z0-9]", "", (sku or "").lower())
    if t.startswith("wos"):
        t = t[3:]
    t = t.replace("case", "")
    if t.endswith("en"):
        t = t[:-2]
    return t


def is_case_row(r):
    return "case" in norm(r["title"]).split() or "case" in (r["sku"] or "").lower()


def find_third_party(entry, records, official_barcodes, official_skus):
    """Retailer rows matching one gap entry, grouped into products. Official
    data always wins. Rows are grouped by normalised SKU (barcode when a store
    lists one) so the same product from two stores, and a box with its case
    configuration, come back as ONE product with every source attached."""
    off_norm = {norm_sku(x) for x in official_skus}
    groups = {}
    for r in records:
        t = " " + norm(r["title"]) + " "
        if not all(" " + m + " " in t for m in entry["match"]):
            continue
        if any(" " + x + " " in t for x in entry.get("exclude", [])):
            continue
        if entry.get("any_of") and not any(" " + x + " " in t for x in entry["any_of"]):
            continue
        if not is_sealed_candidate(r["title"]):
            continue
        ns = norm_sku(r["sku"])
        if (r["barcode"] and r["barcode"] in official_barcodes) or (ns and ns in off_norm):
            continue
        key = r["barcode"] or ns or (r["store"], r["url"])
        g = groups.setdefault(key, {"rows": []})
        if not any(x["store"] == r["store"] and x["url"] == r["url"] and x["sku"] == r["sku"] for x in g["rows"]):
            g["rows"].append(r)
    products = []
    for key, g in groups.items():
        rows = g["rows"]
        main = next((x for x in rows if not is_case_row(x)), rows[0])
        products.append({"key": key, "main": main, "rows": rows})
    return products



# ---------------------------------------------------------------------
# OFFICIAL card API (api.gatcg.com): set release dates + card counts + rarity
# splits for the gap products. Validated against the official product sheets on
# 7 Oct 2026 (release dates matched for every set compared; rarity splits matched
# exactly for Abyssal Heaven, Distorted Reflections, .asphodel/paradise and
# Mortal Ambition 1E when the main set and its "1st" set are combined).
# Rarity ids confirmed from /option/search.
# ---------------------------------------------------------------------
API = "https://api.gatcg.com"
_RARITY = {1: "C", 2: "U", 3: "R", 4: "SR", 5: "UR", 6: "PR", 7: "CSR", 8: "CUR", 9: "CPR"}
_RARITY_ORDER = ["C", "U", "R", "SR", "UR", "PR", "CSR", "CUR", "CPR"]
# gap-entry label -> product groups; a group with several prefixes is also
# printed combined (main set + its "1st" collector-rarity set).
API_GROUPS = {
    "Dawn of Ashes (all products)": [
        ("1E booster", ["DOA 1st"]), ("Alter Edition booster", ["DOA Alter"]),
        ("Starter Decks (all decks combined)", ["DOASD"]), ("Prelude", ["DOAp"])],
    "Alchemical Revolution (1E booster + starter decks)": [
        ("1E booster", ["ALC", "ALC 1st"]), ("Starter Decks (all decks combined)", ["ALCSD"])],
    "Mercurial Heart (boosters + decks)": [
        ("1E booster", ["MRC", "MRC 1st"]), ("Alter Edition booster", ["MRC Alter"])],
    "Silvie Re:Collection deck": [("deck", ["ReC-SLM"])],
    "Tristan Re:Collection deck": [("deck", ["ReC-SHD"])],
    ".asphodel/paradise (SKU/UPC/MSRP + starter decks)": [
        ("1E booster", ["PRD", "PRD 1st"]), ("Pantheon subset", ["PRDP"]),
        ("Starter Decks (all decks combined)", ["PRDSD"])],
    "Guo Jia Re:Collection deck": [("deck", ["ReC-HVF"])],
    # Added 14 Oct 2026: "DTR Alter" is in /option/search, so this gap now gets
    # its release date + card counts even with no retailer listing.
    "Distorted Reflections Alter Edition": [("Alter Edition booster", ["DTR Alter"])],
    "Idyll Corsage Re:Collection deck": [("deck", ["ReC-IDY"])],
    "Merlin Re:Collection deck": [("deck", ["ReC-BRV"])],
    "Mordred Re:Collection deck": [("deck", ["ReC-AUR"])],
    "Pantheon Decks (Lorraine/Zander/Arisanna/Kongming)": [("4 Pantheon decks (combined)", ["RDOPD"])],
}
_API_CACHE = {}


def fmt_api_date(iso, prefix):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return "(none)"
    if m.group(1) == "1970":
        return "(not set in API)"
    mons = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    txt = f"{int(m.group(3)):02d} {mons[int(m.group(2)) - 1]} {m.group(1)}"
    if prefix in ("DOA 1st", "DOAp"):
        txt += " (1 Jan is probably a placeholder: Kickstarter product, exact day never published per your tracker)"
    return txt


def tally_api(items):
    """prefix -> {name, release, rows{rar:n}, names{rar:set}, all_names:set, seen:set}"""
    sets = {}
    for it in items:
        eds = it.get("result_editions") if isinstance(it.get("result_editions"), list) else it.get("editions", [])
        for ed in eds or []:
            st = ed.get("set") or {}
            pre = st.get("prefix")
            if not pre:
                continue
            d = sets.setdefault(pre, {"name": st.get("name"), "release": st.get("release_date"),
                                      "rows": {}, "names": {}, "all_names": set(), "seen": set()})
            key = ed.get("uuid") or ed.get("slug")
            if key in d["seen"]:
                continue
            d["seen"].add(key)
            r = _RARITY.get(ed.get("rarity"), str(ed.get("rarity")))
            d["rows"][r] = d["rows"].get(r, 0) + 1
            d["names"].setdefault(r, set()).add(it.get("name"))
            d["all_names"].add(it.get("name"))
    return sets


def fetch_api_prefix(prefix, out=print, max_pages=20):
    """Cards of ONE set prefix (e.g. "ReC-AUR") from /cards/search?prefix=...

    FIXED 13 Oct 2026: the old version swept the WHOLE card database
    (about 100 pages, 40s timeout and 3 retries each, no progress output)
    the first time any gap needed API data -- the run looked frozen right
    after the first third-party block. The API takes a `prefix` filter
    (confirmed: ?prefix=ReC-AUR returns only Mordred deck cards), so each
    set is now fetched on its own: a few requests per set, with a progress
    line, 15s timeout and 2 tries. Cached per run."""
    if prefix in _API_CACHE:
        return _API_CACHE[prefix]
    out(f"    (fetching card API for {prefix}...)")
    items, err = [], None
    for page in range(1, max_pages + 1):
        js = None
        for attempt in range(2):
            try:
                r = session.get(API + "/cards/search",
                                params={"prefix": prefix, "page": page, "page_size": 50, "separate_editions": "true"},
                                headers={"Accept": "application/json"}, timeout=15)
                if r.status_code == 200:
                    js = r.json()
                    break
                err = f"HTTP {r.status_code} on page {page}"
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            time.sleep(1)
        if js is None:
            break
        data = js.get("data") or []
        err = None
        items.extend(data)
        if not data or len(data) < 50 or js.get("has_more") is False:
            break
    tallied = tally_api(items) if items else {}
    result = (tallied.get(prefix), err)
    _API_CACHE[prefix] = result
    return result


def _split(counts):
    parts = [f"{counts[r]}{r}" for r in _RARITY_ORDER if counts.get(r)]
    return " / ".join(parts) if parts else "(none)"


def emit_api_block(entry, out):
    groups = API_GROUPS.get(entry["label"])
    if groups is None:
        return
    out("  --- OFFICIAL API data (api.gatcg.com): set release date, card counts, rarity split ---")
    sets, errs = {}, []
    for _, prefixes in groups:
        for pre in prefixes:
            d, err = fetch_api_prefix(pre, out)
            if d:
                sets[pre] = d
            elif err:
                errs.append(f"{pre}: {err}")
    if not sets:
        out(f"  [API unavailable: {'; '.join(errs) or 'no data'}] -- release dates/counts NOT filled for this gap.")
        return
    for glabel, prefixes in groups:
        found = [(p, sets[p]) for p in prefixes if p in sets]
        out(f"  {glabel}:")
        if not found:
            out(f"    API has no set for {prefixes} -- NOT filled (not guessed).")
            continue
        for p, d in found:
            names_by_r = {r: len(n) for r, n in d["names"].items()}
            out(f"    [{p}] {d['name']} -- release date: {fmt_api_date(d['release'], p)}")
            out(f"      distinct cards: {len(d['all_names'])} | edition rows: {sum(d['rows'].values())}")
            out(f"      rarity split (distinct cards): {_split(names_by_r)}")
            if names_by_r != d["rows"]:
                out(f"      rarity split (edition rows):   {_split(d['rows'])}")
        if len(found) > 1:
            comb = {}
            allnames = set()
            for _, d in found:
                allnames |= d["all_names"]
                for r, n in d["names"].items():
                    comb.setdefault(r, set()).update(n)
            out(f"    combined {' + '.join(p for p, _ in found)}: distinct cards {len(allnames)} | "
                f"rarity split (distinct cards): {_split({r: len(n) for r, n in comb.items()})}")
        if glabel.startswith("Alter Edition") and found and sum(found[0][1]["rows"].values()) < 100:
            out("    NOTE: this set holds only a fraction of the Alter Edition's cards (the new/changed ones), "
                "so it is NOT the full-set rarity split.")
    out("    Caveat: API counts cards in the official card database; they can differ by 1-2 from a product "
        "sheet's own totals. Starter/Pantheon decks are combined per set (per-deck split not in API).")


_STORE_CACHE = {}


def load_store_records(out):
    """All retailer catalog rows, fetched once per run (used for product
    images AND the third-party fallback)."""
    if "records" not in _STORE_CACHE:
        records = []
        for name, url in THIRD_PARTY_STORES:
            recs, err = fetch_store_catalog(name, url)
            records.extend(recs)
            out(f"  Store {name}: " + (f"ERROR {err}" if err else f"{len(recs)} variant row(s) loaded"))
        _STORE_CACHE["records"] = records
    return _STORE_CACHE["records"]


def _match_by_name(p, records):
    """Last resort for official products whose UPC/SKU no retailer lists
    (e.g. .asphodel/paradise's sheet has no SKU/UPC at all): a non-case
    retailer row whose title names the set AND the product -- the deck's
    champion ("kongming") for starter decks, "booster box" for boosters --
    with "alter" in the title only for an Alter Edition."""
    set_n = norm(p["set_name"]).replace("asphodel ", "")
    want = ["booster box"] if p["product_type"] == "Booster Pack" else []
    if p["product_type"] == "Starter Deck":
        champ = norm(p["product_name"]).replace(" starter deck", "").split(" ")[0]
        want = [champ, "starter"]
    if not want:
        return None
    alter = (p["edition"] == "Alter Edition")
    for r in records:
        if not r.get("image") or is_case_row(r):
            continue
        t = " " + norm(r["title"]) + " "
        if f" {set_n} " not in t and p["product_type"] != "Starter Deck":
            continue
        if not all(f" {w} " in t for w in want):
            continue
        if ((" alter " in t) != alter) or " lite " in t:
            continue
        return r
    return None


def attach_images(products, records):
    """Per-product image: a retailer listing photo whose barcode equals one of
    the product's official UPCs (box/deck UPC tried first), else whose SKU
    normalises to the official SKU. Plus the official set cover."""
    by_barcode = {r["barcode"]: r for r in records if r.get("barcode") and r.get("image")}
    by_sku = {}
    for r in records:
        if r.get("image") and r.get("sku") and not is_case_row(r):
            by_sku.setdefault(norm_sku(r["sku"]), r)

    def upc_order(item):
        label = item[0].lower()
        if "case" in label:
            return 3
        if "box" in label or ("deck" in label and "display" not in label):
            return 0
        if "pack" in label:
            return 1
        return 2

    for p in products:
        info_url = dict(KNOWN_SETS).get(p["set_name"])
        p["set_cover"] = SET_COVERS.get(info_url)
        hit, how = None, None
        for label, code in sorted(p["upcs"], key=upc_order):
            if code in by_barcode:
                hit, how = by_barcode[code], f"UPC {code} ({label})"
                break
        if not hit and p["sku"] and norm_sku(p["sku"]) in by_sku:
            hit, how = by_sku[norm_sku(p["sku"])], f"SKU {p['sku']}"
        if not hit:
            hit = _match_by_name(p, records)
            if hit:
                how = f"title '{hit['title']}' (name match -- no UPC/SKU match; check it)"
        if hit:
            p["image"], p["image_source"], p["image_match"] = hit["image"], hit["store"], how


def api_future_release(entry, out):
    """Release date if EVERY API set behind this gap releases after today
    (e.g. Distorted Reflections Alter Edition: 16 Oct 2026, seen in the
    14 Oct 2026 run) -- such gaps are skipped like unreleased sets. They come
    back automatically once the date passes. None otherwise."""
    from datetime import date
    groups = API_GROUPS.get(entry["label"])
    if not groups:
        return None
    dates = []
    for _, prefixes in groups:
        for pre in prefixes:
            d, _err = fetch_api_prefix(pre, out)
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", (d or {}).get("release") or "")
            if not m:
                return None
            dates.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    if dates and min(dates) > date.today():
        return fmt_api_date(min(dates).isoformat(), "")
    return None


def run_third_party_fallback(targets, official_barcodes, official_skus, out):
    out(f"\n{'=' * 70}")
    out("THIRD-PARTY FALLBACK -- NOT OFFICIAL (retailer catalog data)")
    out("Used only because the official source cannot supply these products.")
    out("Retailer SKU may be the store's own code.")
    out(f"{'=' * 70}")
    records = list(load_store_records(out))
    seen_urls = set()
    for entry in targets:
        if find_third_party(entry, records, official_barcodes, official_skus):
            continue
        for purl in entry.get("product_urls", []):
            if purl in seen_urls:
                continue
            seen_urls.add(purl)
            recs, err = fetch_product_json(purl)
            records.extend(recs)
            out(f"  Direct product page {purl}: " + (f"ERROR {err}" if err else f"{len(recs)} variant row(s)"))
    unresolved = []
    printed = set()  # a product is shown once, under the first gap it matched
    for entry in targets:
        future = api_future_release(entry, lambda *_: None)  # silent; data is cached for the block below
        if future:
            out(f"\n[{entry['label']}] -- not released yet (GA card database: {future}) -- SKIPPED.")
            continue
        prods = [pr for pr in find_third_party(entry, records, official_barcodes, official_skus)
                 if pr["key"] not in printed]
        out(f"\n[{entry['label']}] -- gap reason: {entry['reason']}")
        if entry.get("release_date"):
            out(f"  Release date (official, GA announcement): {entry['release_date']}  -- {entry['release_source']}")
        else:
            # The set page's date is the set's FIRST release -- wrong for an
            # Alter Edition or a later deck, so it is only shown otherwise.
            page_rel = SET_PAGE_RELEASE.get(dict(KNOWN_SETS).get(entry["set"]))
            if page_rel and "alter" not in entry["label"].lower():
                out(f"  Release date (official, from GA's set page text): {page_rel}")
        if not prods:
            out("  No third-party match found -- STILL UNRESOLVED (flagged, not guessed).")
            unresolved.append(entry["label"])
            emit_api_block(entry, out)
            continue
        for pr in prods:
            printed.add(pr["key"])
            m = pr["main"]
            out(f"  [THIRD-PARTY] {m['title']}")
            img = m.get("image") or next((r.get("image") for r in pr["rows"] if r.get("image")), None)
            out(f"    Image: {img or '(none on listing)'}")
            out(f"    Vendor: {m['vendor']}  |  Type: {m['product_type']}")
            for r in pr["rows"]:
                role = " [case configuration]" if is_case_row(r) else ""
                out(f"    Source: {r['store']} -- SKU {r['sku'] or '(none)'} -- {r['price']} (retailer price, not MSRP)"
                    f" -- store listing created {str(r['published_at'])[:10]} (NOT a release date){role}")
                out(f"      {r['url']}")
        emit_api_block(entry, out)
    return unresolved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="Optional output file path (written as UTF-8 directly)")
    args = parser.parse_args()

    out_f = open(args.out, "w", encoding="utf-8", errors="replace", newline="\n") if args.out else None

    def out(line=""):
        print(line)
        if out_f:
            out_f.write(line + "\n")

    query = input("Enter a Grand Archive product name or SKU to search for (blank = ALL products): ").strip().lower()

    all_products = []
    flagged_sets = []

    from datetime import datetime
    unreleased = set()
    for set_name, info_url in KNOWN_SETS:
        out(f"\n{'=' * 70}")
        out(f"Checking: {set_name}")
        try:
            pdf_links = fetch_pdf_links(info_url)
        except Exception as e:
            out(f"  [ERROR fetching product-information page: {e}] -- FLAGGED for manual research.")
            flagged_sets.append((set_name, f"Could not fetch product-information page: {e}"))
            continue

        # Not-yet-released sets are skipped entirely (instruction 14 Oct 2026:
        # ".asphodel/cantabile ... its not out yet"). Decided from the set
        # page's own "releases on <date>" text, so cantabile is picked up
        # automatically once that date has passed -- no code change needed.
        page_rel = SET_PAGE_RELEASE.get(info_url)
        try:
            rel_dt = datetime.strptime(page_rel, "%d %b %Y") if page_rel else None
        except ValueError:
            rel_dt = None
        if rel_dt and rel_dt.date() > datetime.now().date():
            out(f"  Not released yet (set page says {page_rel}) -- SKIPPED.")
            unreleased.add(set_name)
            continue

        if not pdf_links:
            out("  [No PDF links found] -- FLAGGED for manual research.")
            flagged_sets.append((set_name, "No PDF linked from product-information page."))
            continue

        set_found_any = False
        for link in pdf_links:
            full_url = link if link.startswith("http") else BASE + link
            # Only infer from the filename when it actually says so (e.g.
            # "_AE_", "ALCAE"). Don't default everything else to "First
            # Edition" -- some sheets (Fractured Crown) predate that
            # convention and never state an edition at all; parse_product_page
            # falls back to this hint only if the page text itself doesn't
            # literally say "First Edition" or "Alter Edition".
            edition_hint = "Alter Edition" if re.search(r'alter|_AE_|AE_Product|AE\d|\bAE\b', link, re.IGNORECASE) else None
            try:
                pages = fetch_pdf_pages_text(full_url, referer=info_url)
            except Exception as e:
                out(f"  [ERROR downloading/parsing {full_url}: {e}] -- FLAGGED for manual research.")
                flagged_sets.append((set_name, f"PDF failed to download/parse ({full_url}): {e}"))
                continue

            pdf_had_data = False
            for page_text in pages:
                if not page_text.strip():
                    continue
                product = parse_product_page(page_text, set_name, edition_hint)
                if product:
                    all_products.append(product)
                    pdf_had_data = True
                    set_found_any = True

            if not pdf_had_data:
                short_name = full_url.split("/")[-1].split("?")[0]
                out(f"  [{short_name}: no parseable product data -- likely no text layer or no structured fields] -- FLAGGED for manual research.")
                flagged_sets.append((set_name, f"PDF has no parseable product data: {full_url}"))

        if not set_found_any:
            out(f"  No usable product data found for {set_name}.")

    all_products = dedupe_products(all_products)

    if query:
        filtered = [
            p for p in all_products
            if query in (p["product_name"] or "").lower()
            or query in (p["sku"] or "").lower()
            or query in (p["set_name"] or "").lower()
        ]
    else:
        filtered = all_products

    out(f"\n{'=' * 70}")
    out("Loading retailer catalogs for product images (GA's own site has none)...")
    attach_images(filtered, load_store_records(out))

    out(f"\n{'=' * 70}")
    out(f"RESULTS: {len(filtered)} product(s) matched" + (f" for \"{query}\"" if query else " (all products)"))
    out(f"{'=' * 70}")
    for p in filtered:
        format_product(p, out)

    if flagged_sets:
        out(f"\n{'=' * 70}")
        out(f"FLAGGED FOR MANUAL RESEARCH ({len(flagged_sets)} item(s)) -- not fabricated, not included above:")
        out(f"{'=' * 70}")
        for set_name, reason in flagged_sets:
            out(f"  - {set_name}: {reason}")

    out(f"\nTotal products discovered (all sets, before filtering): {len(all_products)}")

    # ---- Third-party redirect --------------------------------------------
    gap_entries = [e for e in KNOWN_UNPULLABLE if e["set"] not in unreleased]
    covered_sets = {p["set_name"] for p in all_products}
    listed_sets = {e["set"] for e in gap_entries}
    for set_name, _ in KNOWN_SETS:  # sets that came back empty this run
        if set_name not in covered_sets and set_name not in listed_sets and set_name not in unreleased:
            gap_entries.append({"set": set_name, "label": f"{set_name} (no official data this run)",
                                "match": [norm(set_name)], "reason": "Official pull returned nothing this run."})
    if not query:
        targets = gap_entries
    else:
        qn = norm(query)
        targets = [e for e in gap_entries if entry_matches_query(e, qn)]
        if not targets and not filtered:
            targets = [{"set": "(search)", "label": f"search: {query}", "match": qn.split(),
                        "reason": "No official match for this search."}]
    if targets:
        official_barcodes = {code for p in all_products for _, code in p["upcs"]}
        official_skus = {p["sku"] for p in all_products if p["sku"]}
        unresolved = run_third_party_fallback(targets, official_barcodes, official_skus, out)
        out(f"\nThird-party summary: {len(targets) - len(unresolved)} of {len(targets)} gap(s) matched, {len(unresolved)} unresolved.")

    if out_f:
        out_f.close()
        print(f"\nWrote output to {args.out}")


if __name__ == "__main__":
    main()