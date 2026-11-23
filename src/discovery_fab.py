"""
Discovery script for Flesh and Blood (FAB) sealed product data.

Confirmed working source (1 Oct 2026): fabtcg.com's own WP REST "product"
endpoint -- https://fabtcg.com/api/wp/v2/product -- per the project's own
confirmed-sources list. (fabrary.net/sets was used for a *different*,
separate project -- a personal FAB Excel release tracker -- not this
migration app; don't confuse the two.)

Key facts about this source, confirmed via live diagnostic dumps the user
ran (inspect_fab_catalog.py / inspect_fab_catalog2.py):

  - 90 total products, fits in a single request with per_page=100 (WP
    REST's usual max) -- no real pagination needed in practice, but
    get_all_products() still paginates properly in case the catalog grows
    past 100.
  - SCOPE LIMITATION, confirmed not a bug: this endpoint's products span
    only ~14 months (2025-06-20 to 2026-08-12) -- it's fabtcg.com's current
    storefront listing, not a historical archive back to the game's 2019
    launch. Pulling "all products" from this source means all *currently
    listed* sealed products, not FAB's full release history. If full
    history is ever needed, that requires a different/additional source.
  - A real `product_type` taxonomy exists (Armory Deck / Blitz Decks /
    Booster Set), fetched via /wp/v2/product_type -- but only 42 of 90
    products have it set. For the rest, the URL's category segment
    (/products/<segment>/...) or a title keyword is used as a fallback.
    See classify_category().
  - No real SKU/barcode is exposed anywhere in this API. The "sku" field
    below is just the WP post ID (an internal CMS id, not a merchant
    SKU/UPC) -- flagging this honestly rather than passing it off as a
    real product code.
  - No reliable release-date text either: the existing "In Stores <date>"
    / "Release date: <date>" regexes only matched 14/90 products in a live
    check. For the rest, there's no confirmed-real date text to extract,
    so we fall back to the post's own `date` field, labelled honestly as
    `listed_date` (the date it was published on the site) rather than
    passing it off as a confirmed retail release date -- same approach
    used for Riftbound's listed_date.
  - content.rendered (already included in the list endpoint -- no need for
    a per-product page fetch) is the real "what's in the box" marketing
    copy, same free-text-prose situation as Riftbound: no structured
    Contents table, so we store the cleaned description text as-is rather
    than forcing it into a fabricated structured list.
  - The catalog includes non-product junk mixed in with real products.
    Confirmed via a live check of all 90 items' content for commerce
    markers ("Barcodes:", "SKU:", "MSRP"): 17 items had none. Two title
    patterns are also excluded directly since the commerce check is a
    content-based safety net, not the only signal: test/placeholder posts
    ("REAL COPY THIS ONE", "copy this one High SEAS" -- these two DO have
    commerce text, copied from a real page, so only the title pattern
    catches them) and internal retailer press-kit pages ("... Product
    Sheets"). Everything else with no commerce markers is excluded
    generically -- see _is_junk_product(). This also resolved what looked
    like genuine duplicate titles: "Armory Deck: Aurora" and "Armory Deck:
    Gravy Bones cards" each had a second entry with the same title but no
    commerce section at all -- those are bare gameplay/decklist companion
    pages for the same product, not separate sealed products, so they're
    excluded too. One edge case is kept rather than silently dropped:
    "Ira Welcome Deck" has no commerce markers either, but welcome/starter
    decks are sometimes genuine no-barcode promotional giveaways, so it's
    flagged in the debug listing for a human check instead of excluded.

UPDATED 16 Nov 2026 (same rules as the other games):
  - contents = the labelled fields from extract_contents() (cards in set,
    set composition, configuration, rarity distribution) instead of the
    whole page text
  - release date = an exact date from the page's "Release date" / "In
    Stores" text, as YYYY-MM-DD; text without an exact day (e.g. "Europe --
    October 2021") gives no date. The site's publish date is NOT used as a
    release date any more (shown in the notes only)
  - image = the post's featured image, or the page's og:image, when the
    site provides one
  - products releasing after today are skipped

Usage:
    python src/discovery_fab.py all --out=fab_output.txt
    python src/discovery_fab.py                  (prompts for a product name; leave blank to pull all)
    python src/discovery_fab.py "Outsiders"       (search within all products, no prompt)
"""

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if any(a.startswith("--out=") for a in sys.argv):
    _out_arg = next(a for a in sys.argv if a.startswith("--out="))
    sys.argv.remove(_out_arg)
    _out_file = open(_out_arg.split("=", 1)[1], "w", encoding="utf-8")

    class _Tee:
        def write(self, s):
            sys.__stdout__.write(s)
            _out_file.write(s)

        def flush(self):
            sys.__stdout__.flush()
            _out_file.flush()

    sys.stdout = _Tee()

import html as html_lib
import re
from datetime import date
import requests
from common import make_product_record, print_product_record

PRODUCT_URL = "https://fabtcg.com/api/wp/v2/product"
PRODUCT_TYPE_TAXONOMY_URL = "https://fabtcg.com/api/wp/v2/product_type"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://fabtcg.com/",
})

# Confirmed junk title patterns from a live full-catalog dump (1 Oct 2026)
# -- these two slip past the commerce-marker check below because the pages
# actually contain real commerce text, copied from whatever page they were
# testing against.
_JUNK_SUBSTRING_PATTERNS = [
    re.compile(r"copy this one", re.IGNORECASE),
]
_JUNK_TITLE_SUFFIXES = ["product sheets"]  # case-insensitive suffix match

# Garbled/placeholder titles that are clearly not real products regardless
# of commerce content (id 91944's title is literally "iar").
_JUNK_EXACT_TITLES = {"iar"}

# Kept despite having no commerce markers -- welcome/starter decks are
# sometimes genuine no-barcode promotional giveaways, so this isn't
# auto-excluded, just flagged in the debug listing for a human check.
_NO_COMMERCE_EXCEPTIONS = {"ira welcome deck"}

_COMMERCE_MARKERS = ("Barcodes:", "SKU:", "MSRP")


def _has_commerce_markers(content_html):
    return any(marker in content_html for marker in _COMMERCE_MARKERS)


def _is_junk_product(title, content_html):
    """True if this item should be excluded as non-product junk -- a test
    post, a retailer press-kit page, a hub/index page, or a bare gameplay/
    decklist companion page for a product already counted elsewhere."""
    title_clean = html_lib.unescape(title or "").strip()
    title_lower = title_clean.lower()

    if title_lower in _JUNK_EXACT_TITLES:
        return True
    if any(title_lower.endswith(suffix) for suffix in _JUNK_TITLE_SUFFIXES):
        return True
    if any(p.search(title_clean) for p in _JUNK_SUBSTRING_PATTERNS):
        return True
    if title_lower in _NO_COMMERCE_EXCEPTIONS:
        return False
    if not _has_commerce_markers(content_html):
        return True
    return False


# URL category segment (/products/<segment>/...) -> readable category, used
# when the product_type taxonomy is empty. "product" is the generic/default
# segment WP falls back to and covers many genuinely different product
# types, so it's handled by title keywords instead (see classify_category).
_URL_CAT_MAP = {
    "armory-deck": "Armory Deck",
    "booster-set": "Booster Set",
    "blitz-decks": "Blitz Decks",
}

# Title-keyword fallback for products under the generic "product" URL
# segment with no taxonomy set. Checked in order, first match wins.
_TITLE_KEYWORDS = [
    ("Blitz Decks", ["blitz deck"]),
    ("Pre-Release Kit", ["pre-release kit", "prerelease kit"]),
    ("Mastery Pack", ["mastery pack"]),
    ("Silver Age Deck", ["silver age"]),
    ("Demo Deck", ["demo deck"]),
    ("Welcome Deck", ["welcome deck"]),
    ("Compendium", ["compendium"]),
    ("Armory Deck", ["armory deck"]),
    ("Booster Set", ["booster"]),
]

# Content-based fallback for products with no taxonomy, no matching url_cat,
# and no title keyword match -- checked against the page's own content text
# (not just the title). Confirmed from live data: "Light & Shadow" and
# "Faith & Torment" are real "Collector Series Set" products (boxes of
# exclusive promo cards) with no title hint at all.
_CONTENT_KEYWORDS = [
    ("Collector Series Set", ["collector series set"]),
]


def classify_category(title, product_type_names, url_cat, content_html=""):
    if product_type_names:
        return "/".join(product_type_names)
    if url_cat and url_cat in _URL_CAT_MAP:
        return _URL_CAT_MAP[url_cat]
    title_lower = (title or "").lower()
    for category, keywords in _TITLE_KEYWORDS:
        if any(kw in title_lower for kw in keywords):
            return category
    content_lower = (content_html or "").lower()
    for category, keywords in _CONTENT_KEYWORDS:
        if any(kw in content_lower for kw in keywords):
            return category
    return "Other"


def fetch_product_type_terms():
    """id -> name map for the product_type taxonomy."""
    resp = session.get(PRODUCT_TYPE_TAXONOMY_URL, params={"per_page": 100}, timeout=15)
    resp.raise_for_status()
    return {t["id"]: t["name"] for t in resp.json()}


_URL_CAT_RE = re.compile(r"/products/([^/]+)/")


def get_all_products():
    """Pull every product from the catalog (paginates properly even though
    the current 90-item total fits in one per_page=100 request), excluding
    confirmed junk -- see _is_junk_product(). No dedup beyond that -- see
    module docstring on why possible-duplicate titles are left in rather
    than guessed away."""
    term_map = fetch_product_type_terms()

    all_items = []
    page = 1
    while True:
        resp = session.get(PRODUCT_URL, params={"per_page": 100, "page": page, "_embed": "wp:featuredmedia"}, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_items.extend(batch)
        total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1

    today = date.today().isoformat()
    products, skipped = [], []
    for item in all_items:
        title = html_lib.unescape(item.get("title", {}).get("rendered", ""))
        content_html = item.get("content", {}).get("rendered", "")
        if _is_junk_product(title, content_html):
            continue

        link = item.get("link", "")
        m = _URL_CAT_RE.search(link)
        url_cat = m.group(1) if m else None
        ptype_names = [term_map.get(pid) for pid in item.get("product_type", []) if pid in term_map]

        listed = (item.get("date") or "")[:10]  # site publish date, NOT a release date
        rel, how = parse_release(content_html, listed)
        if rel and rel > today:
            skipped.append((title, f"releases {rel}"))
            continue
        image, image_how = product_image(item)
        products.append({
            "id": item.get("id"),
            "title": title,
            "category": classify_category(title, ptype_names, url_cat, content_html),
            "listed_date": listed,
            "release_date": rel,
            "release_how": how,
            "image": image,
            "image_how": image_how,
            "contents": extract_contents(content_html),
            "content_html": content_html,
            "source_url": link,
            "needs_human_check": title.strip().lower() in _NO_COMMERCE_EXCEPTIONS,
        })
    get_all_products.skipped = skipped
    return products


_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]
_EXACT_DATE_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
    r"(?:\s*[-\u2013]\s*\d{1,2}(?:st|nd|rd|th)?)?,?\s+(\d{4})", re.IGNORECASE)


def parse_release(content_html, listed_date):
    """-> (YYYY-MM-DD or None, note). Only an exact day counts as a release
    date; the first one in the page's release-date text is used (for a date
    range like "July 24-27, 2025", its first day)."""
    raw = extract_release_date(content_html)
    if not raw:
        return None, f"no release date on the product page (site publish date {listed_date or '?'} is not a release date)"
    found = []
    for m in _EXACT_DATE_RE.finditer(raw):
        try:
            found.append(date(int(m.group(3)), _MONTHS.index(m.group(1).lower()) + 1, int(m.group(2))).isoformat())
        except ValueError:
            pass
    if not found:
        return None, f'no exact date on the product page: "{raw}"'
    return found[0], f'product page: "{raw}"' + (" (first date given)" if len(found) > 1 else "")


def product_image(item):
    """Featured image (via _embed), else the page's og:image, else None."""
    media = ((item.get("_embedded") or {}).get("wp:featuredmedia") or [])
    if media and isinstance(media[0], dict) and str(media[0].get("source_url") or "").startswith("http"):
        return media[0]["source_url"], "featured image"
    og = ((item.get("yoast_head_json") or {}).get("og_image") or [])
    if og and isinstance(og[0], dict) and str(og[0].get("url") or "").startswith("http"):
        return og[0]["url"], "page og:image"
    return None, "no image on the site"


def _first_overview_block(text):
    """Products sold in more than one version list a second overview block
    ("Overview – High Seas Booster (JP)", Super Slam JP, Armory Deck:
    Legends' second deck). Only the first block is parsed, so the EN figures
    aren't mixed with the JP ones."""
    starts = [m.start() for m in re.finditer(r"\bOverview\b", text)]
    if len(starts) >= 2:
        return text[starts[0]:starts[1]]
    if starts:
        return text[starts[0]:]
    return text


def extract_release_date(content_html):
    """FIXED (9 Oct 2026, live run): the old patterns only took
    "Release date: <Month D, YYYY>". Two real layouts fell back to the
    site's publish date (2025-07-08) instead:
      Tales of Aria Unlimited -- "Release date: Europe, UK, South East Asia
        – October 2021 (...) North America – November ..."
      Armory Deck: Legends -- "Release dates: San Diego Comic Con (July
        24-27, 2025) Gen Con (July 31 – August 3, 2025)"
    The release-date text is now taken up to the next field label and kept
    as-is (not reformatted), same as the Digimon script."""
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", content_html))
    text = _first_overview_block(re.sub(r"\s+", " ", text))
    m = re.search(
        r"Release dates?\s*:\s*(.*?)(?=\s*(?:Set code|Product type|Available languages|Cards in set|"
        r"MSRP|Cards become|Barcodes|Key Dates|Product Details|SKU:|Pre-?Release|$))",
        text, re.IGNORECASE,
    )
    if m and re.search(r"\d{4}", m.group(1)) and len(m.group(1)) <= 200:
        return m.group(1).strip().rstrip(".,")
    m = re.search(r"In Stores\s*([A-Za-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Contents parsing (NEW, 9 Oct 2026)
#
# content.rendered is the whole product page: marketing copy, hero blurbs,
# deck-building tips, barcodes, credits. Confirmed from a live run of all 44
# listed products that the useful facts always sit in LABELLED fields inside
# the page's "Overview" block:
#   Cards in set: 265                        (or "536 card set")
#   Set Composition: / Set Configuration:     -> rarity breakdown
#       "1 Fabled 6 Legendary 46 Majestic 64 Rares 127 Commons 18 Basic 21 Marvels"
#   Set contains 3 Fabled, 14 Legendary, ...  (History Pack 2)
#   Product Configuration: / Box set Configuration: / Pack Configuration:
#       -> what's in the pack/box/deck and per-display/case counts
#   Rarity Distribution:                      -> pull rates per pack
# Only those fields are kept; everything else (marketing, credits, barcodes,
# card spotlights) is dropped. Nothing is invented -- a field missing from
# the page is simply absent from the output.
# ---------------------------------------------------------------------------
_FIELD_LABEL_RE = re.compile(
    r"(?:\b(?:EN|JP)\s+)?"
    r"(?P<label>Cards in set|Set Composition|Rarity Distribution|Set contains|"
    r"(?:Set|Product|Pack|Box set|SET)\s+Configuration)\s*:?",
    re.IGNORECASE,
)
# Text that ends a field's value. Anything after these is page furniture.
_FIELD_STOP_RE = re.compile(
    r"\s*(?:\bStated card drop|\bNo specific card|\bResources\b|\bCredits\b|\bMarketing Assets\b|"
    r"\bProduct Sheet\b|\bHow to Play\b|\bArmory Deck Reveal\b|\bUpgrade Your Deck\b|\bBooster Packs\b|"
    r"\bFind an Armory\b|\bLearn to Play\b|\bMSRP\b|\bBarcodes:|\bSKU:|\bDesigned for\b|\bDue to\b|"
    r"\bDeck List\b|\bPlease note\b|\bAvailable (?:free|languages)\b|\bUPP\b|\bMAP\b|\bLocal Play\b|"
    r"\bPreview Season\b|\bSpoilers\b|\bRelease Notes\b|\bOverview\b|\*Due|\*[A-Z])",
)
_RARITY_ITEM_RE = re.compile(r"(\d+|\?\?\?)\*{0,2}\s+([A-Za-z][A-Za-z /]*?)(?=\s*,?\s*(?:\d|\?\?\?)|\s*[.(]|\s*$)")
_WORDS_BEFORE_COUNT_OK = {"containing", "contains", "contain", "of", "with", "plus", "and", "per", "to", "being", "includes", "include"}


def _clean_value(value):
    value = value.strip().lstrip(":").strip()
    m = _FIELD_STOP_RE.search(value)
    if m:
        value = value[:m.start()]
    # Drop a dangling "EN"/"JP" that belongs to the NEXT label.
    value = re.sub(r"\s+(?:EN|JP)\s*$", "", value)
    return value.strip().rstrip(".,;:(").strip()


def _split_items(value):
    """'12 Mastery Pack Guardian booster packs 1 40-card Event Deck 6 Hero
    cards' -> '12 Mastery Pack Guardian booster packs; 1 40-card Event Deck;
    6 Hero cards'. Splits only where a new counted item starts (a number
    right after a word or ')'), never inside "each containing 40 cards"."""
    out = []
    tokens = value.split(" ")
    for i, tok in enumerate(tokens):
        starts_item = (
            i > 0
            and re.match(r"^\d+\*{0,2}(?:[-x]|$)", tok)
            and re.search(r"[A-Za-z)\]*]$", tokens[i - 1])
            and tokens[i - 1].lower().strip(",:") not in _WORDS_BEFORE_COUNT_OK
        )
        if starts_item:
            out.append("; ")
        elif i > 0:
            out.append(" ")
        out.append(tok)
    return "".join(out)


def _format_rarity_list(value):
    """'1 Fabled 6* Legendary 46 Majestic' -> '1 Fabled, 6 Legendary, 46
    Majestic'. Falls back to the cleaned text if it doesn't look like a
    count list."""
    # Drop parenthetical notes inside the list (Arcane Rising 1st Ed has
    # "1 Fabled ( No specific card ... case. ) 14 Tokens ...").
    value = re.sub(r"\([^)]*\)", " ", value)
    items = [f"{n} {name.strip()}" for n, name in _RARITY_ITEM_RE.findall(value)]
    return ", ".join(items) if len(items) >= 2 else value.strip()


def _format_distribution(value):
    """'Rainbow Foil – 1 per pack Rare or higher – 2 per pack (...)' ->
    one item per slot, separated by '; '."""
    # Split only after the end of a slot ("... per pack", "...)"), so a slot
    # name like "Rainbow Foil" isn't cut in two (fixed 16 Nov 2026).
    return re.sub(r"(\b(?:packs?|displays?|cases?|box(?:es)?)\b|\))\s+(?=[A-Z][A-Za-z /]{1,40}? – )", r"\1; ", value).strip()


def extract_contents(content_html):
    """Returns a list of short labelled lines: card count, rarity
    breakdown, pack/product configuration and pull rates -- only the ones
    the page actually states."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content_html, flags=re.DOTALL | re.IGNORECASE)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    text = _first_overview_block(re.sub(r"\s+", " ", text))

    labels = list(_FIELD_LABEL_RE.finditer(text))
    fields = {}
    for i, m in enumerate(labels):
        end = labels[i + 1].start() if i + 1 < len(labels) else len(text)
        raw = text[m.end():end]
        label = m.group("label").lower()
        value = _clean_value(raw)
        if not value:
            continue

        if label == "cards in set":
            n = re.match(r"(\d+)", value)
            if n:
                fields.setdefault("cards", n.group(1))
        elif label in ("set composition", "set contains"):
            fields.setdefault("rarity", _format_rarity_list(value))
        elif label == "rarity distribution":
            fields.setdefault("distribution", _format_distribution(value))
        else:  # any "... Configuration"
            # "Set Configuration" is used for BOTH the rarity list and the
            # pack layout on older pages (Crucible of War lists it twice).
            # A value that is a plain count list of rarities is a rarity
            # breakdown; anything describing packs/decks is configuration.
            looks_like_pack = re.search(r"per (?:pack|display|case|deck)|booster|deck|contains|cards in set|playmat", value, re.IGNORECASE)
            if label.startswith("set") and not looks_like_pack:
                fields.setdefault("rarity", _format_rarity_list(value))
            else:
                fields.setdefault("config", _split_items(value))

    # "536 card set" (History Pack 2 Black Label) has no "Cards in set" label.
    if "cards" not in fields:
        m = re.search(r"\b(\d+) card set\b", text)
        if m:
            fields["cards"] = m.group(1)

    lines = []
    if "cards" in fields:
        lines.append(f"Cards in set: {fields['cards']}")
    if "rarity" in fields:
        lines.append(f"Set composition: {fields['rarity']}")
    if "config" in fields:
        lines.append(f"Configuration: {fields['config']}")
    if "distribution" in fields:
        lines.append(f"Rarity distribution: {fields['distribution']}")
    # Any single line this long means a stop phrase is missing -- dropped
    # rather than kept as a paragraph dump, and reported in the summary.
    return [ln for ln in lines if len(ln) <= 400]


def extract_description(content_html):
    """Clean marketing description from the product page's own content
    block -- same free-text-prose situation as Riftbound (no structured
    Contents table), so this stores cleaned text as-is rather than forcing
    a fabricated structured list."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def search_products(products, search_term):
    search_norm = search_term.lower()
    return [p for p in products if search_norm in p["title"].lower()]


def resolve_match(matches, search_term):
    if len(matches) == 1:
        return matches[0]

    search_norm = search_term.lower()
    exact = [m for m in matches if m["title"].lower() == search_norm]
    if len(exact) == 1:
        return exact[0]

    print(f"\n'{search_term}' matched {len(matches)} products — which one did you mean?")
    for i, m in enumerate(matches, start=1):
        print(f"  {i}. [{m['category']}] {m['title']}  (listed {m['listed_date']})")
    choice = input(f"Enter number (1-{len(matches)}), or blank to cancel: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(matches)):
        print("Cancelled.")
        return None
    return matches[int(choice) - 1]


def build_record(product):
    return make_product_record(
        game="Flesh and Blood",
        title=product["title"],
        sku=product["id"],  # WP post ID, NOT a real merchant SKU/UPC -- none exposed by this API
        release_date=product["release_date"],
        image_url=product["image"],
        contents=product["contents"],
        pack_configuration=[],
        price=None,  # no price field exposed by this API
        source_url=product["source_url"],
    )


def discover_all():
    products = get_all_products()
    print(f"[DEBUG] Found {len(products)} products after excluding known junk:")
    for p in products:
        flag = "  <-- NO COMMERCE DATA, please verify this is a real product" if p["needs_human_check"] else ""
        print(f"  - [{p['category']}] '{p['title']}'  (id {p['id']}, listed {p['listed_date']}){flag}")

    records = []
    for p in products:
        record = build_record(p)
        print_product_record(record)
        print(f"Category: {p['category']} | Release note: {p['release_how']} | Image: {p['image_how']}")
        records.append(record)
    skipped = getattr(get_all_products, "skipped", [])
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for n, why in skipped:
        print(f"  - {n}: {why}")
    for label, test in (("no exact release date", lambda p: not p["release_date"]),
                        ("no image", lambda p: not p["image"]),
                        ("no contents", lambda p: not p["contents"])):
        bad = [p for p in products if test(p)]
        print(f"\n{len(bad)} product(s) with {label}:")
        for p in bad:
            print(f"  - {p['title']}  {p['release_how'] if 'date' in label else p['source_url']}")
    return records


def discover(search_term):
    products = get_all_products()
    matches = search_products(products, search_term)

    if not matches:
        print(f"No product found matching '{search_term}'")
        return

    match = resolve_match(matches, search_term)
    if match is None:
        return

    record = build_record(match)
    print_product_record(record)
    print(f"Category: {match['category']} | Release note: {match['release_how']} | Image: {match['image_how']}")
    return record


if __name__ == "__main__":
    # A CLI arg still works (python discovery_fab.py "Outsiders") and skips
    # the prompt entirely -- use this if you're redirecting output to a file
    # (python discovery_fab.py "" > out.txt won't work for "all", but running
    # with no args and piping blank input in will, e.g. echo. | python ...).
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() == "all":
        discover_all()
    elif len(sys.argv) >= 2:
        discover(sys.argv[1])
    else:
        search_term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        if search_term:
            discover(search_term)
        else:
            discover_all()