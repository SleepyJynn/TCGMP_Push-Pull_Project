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

Usage:
    python src/discovery_fab.py                  (prompts for a product name; leave blank to pull all)
    python src/discovery_fab.py "Outsiders"       (search within all products, no prompt)
    python src/discovery_fab.py all --out=fab_output.txt   (pull everything into a UTF-8 file)
    python src/discovery_fab.py 79660 --debug --out=dbg.txt (one product by its id, with debug)
"""

import html as html_lib
import re
import sys

# Same --out=<file> option as the Digimon script: writes the output as UTF-8
# to a file (and still prints it), so long runs don't get cut off by the
# terminal. e.g.  python src/discovery_fab.py --out=fab_output.txt
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

# --debug: with a single-product search, also prints every image candidate
# with its score and the full page text, e.g.
#   python src/discovery_fab.py "Silver Age" --debug --out=fab_debug.txt
_DEBUG = "--debug" in sys.argv
if _DEBUG:
    sys.argv.remove("--debug")

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


# ---------------------------------------------------------------------------
# Image -- REWRITTEN (9 Oct 2026, after the first live run)
#
# The first version took the first image it found (featured image, then the
# Yoast preview image, then the first <img> in the content). The live run
# showed that on several newer pages that picks a page background or a
# cross-promo picture of a DIFFERENT product:
#   Armory Deck – Dr. Mortimer      -> ad_azalea_texture_1...webp
#   Mastery Pack Assassin           -> dr-mortimer-cover-scaled.webp
#   Smash Palace (2026)             -> AD_Olympia_Banner_Background.webp
#   Silver Age – Usurp the Shadow Throne -> AD-Malice-Banner-Background...jpg
# Now every candidate from all three sources is collected and scored on its
# FILE NAME:
#   + product-name words in the filename ("mortimer", "assassin", "usurp")
#   + package-shot words (mockup, productshot, dropshadow, booster, display...)
#   - background/banner/texture/cover/logo/masthead words
#   - another product's name, when the filename names a known product and
#     not this one
# The winner's source and filename are kept, and a full run lists every
# product whose chosen image doesn't mention the product's name, so a human
# can eyeball those few. No image is ever invented.
# ---------------------------------------------------------------------------
_CONTENT_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
_GOOD_IMG_WORDS = ("mockup", "mock_", "productshot", "product_shot", "dropshadow", "booster", "display",
                   "box", "pack", "deck", "collection", "packaging", "product")
_BAD_IMG_WORDS = ("texture", "background", "banner", "_bg", "-bg", "bg-", "cover", "logo", "masthead",
                  "art_sig", "paper_", "icon", "avatar", "header", "chapter-bg")
# Words that appear in many product names and say nothing about WHICH product.
_GENERIC_TITLE_WORDS = {"armory", "deck", "decks", "pack", "packs", "mastery", "booster", "blitz",
                        "collection", "origins", "the", "and", "of", "first", "edition", "unlimited",
                        "set", "silver", "age", "black", "label", "demo", "welcome", "card", "cards"}


def _title_tokens(title):
    """Name words that identify THIS product in an image filename. fabtcg's
    filenames often abbreviate, so besides the full words this also adds
    the first 3 letters of each word ("monarch" -> "mon", "uprising" ->
    "upr") and the title's initials ("Tales of Aria" -> "toa", "Dusk till
    Dawn" -> "dtd", "Welcome to Rathe" -> "wtr")."""
    words = re.findall(r"[a-z0-9]+", html_lib.unescape(title).lower())
    full = {w for w in words if len(w) >= 4 and w not in _GENERIC_TITLE_WORDS}
    tokens = set(full) | {w[:3] for w in full}
    # Initials of every leading run of words, so "Tales of Aria Unlimited"
    # still yields "toa" and "History Pack 1" yields "hp1".
    for k in range(2, len(words) + 1):
        tokens.add("".join(w[0] for w in words[:k]))
    tokens |= {w for w in words if len(w) == 3 and w not in _GENERIC_TITLE_WORDS}  # "Ira"
    return tokens


def _image_candidates(item, content_html):
    out = []
    for media in (item.get("_embedded") or {}).get("wp:featuredmedia") or []:
        if isinstance(media, dict) and media.get("source_url"):
            out.append(("featured", media["source_url"]))
    for og in (item.get("yoast_head_json") or {}).get("og_image") or []:
        if isinstance(og, dict) and og.get("url"):
            out.append(("preview", og["url"]))
    for src in _CONTENT_IMG_RE.findall(content_html or ""):
        out.append(("page", html_lib.unescape(src)))
    seen, unique = set(), []
    for source, url in out:
        if url not in seen:
            seen.add(url)
            unique.append((source, url))
    return unique


def _score_image(url, source, own_tokens, other_tokens):
    name = url.rsplit("/", 1)[-1].lower()
    name_words = set(re.findall(r"[a-z0-9]+", name))
    score = 0
    # Short tokens (abbreviations) must match a whole filename word, so
    # "out" doesn't match inside "layout"; long ones may match inside one.
    mentions_self = any(t in name_words if len(t) <= 3 else t in name for t in own_tokens)
    if mentions_self:
        score += 10
    elif {t for t in other_tokens if len(t) >= 4} & name_words:
        score -= 8  # names a different product
    score += 3 * sum(w in name for w in _GOOD_IMG_WORDS if w != "product")
    score -= 6 * sum(w in name for w in _BAD_IMG_WORDS)
    score += {"featured": 2, "preview": 1, "page": 0}[source]
    return score, mentions_self


_SET_CODE_RE = re.compile(r"Set code:\s*([A-Z0-9]{2,5})\b")


def choose_image(item, content_html, title, other_tokens):
    """Returns (url, source, mentions_product_name, all_candidates)."""
    candidates = _image_candidates(item, content_html)
    if not candidates:
        return None, None, False, []
    own = _title_tokens(title)
    # fabtcg file names often use the set code instead of the name
    # ("hnt_booster_dropshadow" = The Hunted, "en_evo_mock_boosterbox" =
    # Bright Lights), so the page's own "Set code: XXX" counts as a name.
    code = _SET_CODE_RE.search(html_lib.unescape(re.sub(r"<[^>]+>", " ", content_html or "")))
    if code:
        own = own | {code.group(1).lower()}
    scored = []
    for order, (source, url) in enumerate(candidates):
        score, mentions = _score_image(url, source, own, other_tokens - own)
        scored.append((score, -order, source, url, mentions))
    scored.sort(reverse=True)
    best = scored[0]
    return best[3], best[2], best[4], [(s, src, u) for s, _, src, u, _ in scored]


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
        # _embed asks WordPress to inline each product's featured image
        # record, so no extra request per product is needed for images.
        resp = session.get(PRODUCT_URL, params={"per_page": 100, "page": page, "_embed": "wp:featuredmedia"}, timeout=20)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_items.extend(batch)
        total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1

    kept = []
    for item in all_items:
        title = html_lib.unescape(item.get("title", {}).get("rendered", ""))
        content_html = item.get("content", {}).get("rendered", "")
        if not _is_junk_product(title, content_html):
            kept.append((item, title, content_html))
    # Name words of every listed product -- used to spot an image that
    # belongs to a DIFFERENT product (see choose_image).
    all_tokens = set()
    for _, title, _ in kept:
        all_tokens |= _title_tokens(title)

    products = []
    for item, title, content_html in kept:

        link = item.get("link", "")
        m = _URL_CAT_RE.search(link)
        url_cat = m.group(1) if m else None
        ptype_names = [term_map.get(pid) for pid in item.get("product_type", []) if pid in term_map]

        products.append({
            "id": item.get("id"),
            "title": title,
            "category": classify_category(title, ptype_names, url_cat, content_html),
            "listed_date": (item.get("date") or "")[:10],  # site publish date, NOT a confirmed retail release date -- see module docstring
            "content_html": content_html,
            "source_url": link,
            **dict(zip(("image_url", "image_source", "image_mentions_product", "image_candidates"),
                       choose_image(item, content_html, title, all_tokens))),
            "has_page_date": extract_release_date(content_html) is not None,
            "needs_human_check": title.strip().lower() in _NO_COMMERCE_EXCEPTIONS,
        })
    return _split_chapter_pages(products)


# ---------------------------------------------------------------------------
# Chapter hub pages (NEW, 12 Oct 2026)
#
# Confirmed via --debug on ids 72075 / 73401 / 79660 / 82561: the four
# "Silver Age" / "Silver Age – Chapter 3" WP entries are NOT four products.
# They are copies of one format hub page, published as it grew, each holding
# a block per real product:
#     PRODUCT DETAILS – CHAPTER 1 Release Date: ... SKU: FAB2602 Product Configuration: ...
#     PRODUCT DETAILS – CHAPTER 2 Release Date: ... SKU: FAB2603 ...
#     PRODUCT DETAILS – CHAPTER 3 Release Date: June 5 2026 ... SKU: FAB2608 ...
# (72075 writes the header as "Product Overview CHAPTER 1 Release Date: ...")
# Parsed as one page, Chapter 1's configuration ran into Chapter 2's and was
# dropped for length. Such a page is now split into one product per chapter
# block, named "<page title base> – Chapter N", and a chapter already taken
# from a newer copy of the page isn't added again. A page with no chapter
# blocks -- e.g. "Silver Age – Usurp the Shadow Throne" -- is left as-is.
# ---------------------------------------------------------------------------
_CHAPTER_BLOCK_RE = re.compile(r"(?:PRODUCT DETAILS\s*[–-]\s*)?CHAPTER\s+(\d+)\s+(?=Release Date)", re.IGNORECASE)
_FAB_SKU_RE = re.compile(r"\bSKU:\s*(FAB\d+(?:-[A-Z]{2})?)")


def _split_chapter_pages(products):
    out, seen_titles = [], set()
    for p in products:
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", p["content_html"]))
        text = re.sub(r"\s+", " ", text)
        starts = list(_CHAPTER_BLOCK_RE.finditer(text))
        if not starts:
            out.append(p)
            continue
        base = re.split(r"\s+[–-]\s+", p["title"])[0].strip()  # "Silver Age – Chapter 3" -> "Silver Age"
        for i, m in enumerate(starts):
            n = m.group(1)
            title = f"{base} – Chapter {n}"
            if title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            block = text[m.end():starts[i + 1].start() if i + 1 < len(starts) else len(text)]
            sku = _FAB_SKU_RE.search(block)
            # Image: only one whose filename names this chapter ("..._ch1_...",
            # "..._ch2_..."); the page's other images are hero deck boxes and
            # backgrounds, so no chapter image -> no image rather than a wrong one.
            ch_img = next((u for _, _, u in p["image_candidates"]
                           if re.search(r"(?:^|[_\-])ch" + n + r"(?:[_\-.]|$)", u.rsplit("/", 1)[-1].lower())), None)
            out.append({
                **p,
                "title": title,
                "id": sku.group(1) if sku else f"{p['id']}-ch{n}",
                "content_html": block,
                "image_url": ch_img,
                "image_source": "page" if ch_img else None,
                "image_mentions_product": bool(ch_img),
                "has_page_date": extract_release_date(block) is not None,
                "needs_human_check": False,
            })
    return out


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
        date = m.group(1).strip().rstrip(".,")
        # An unclosed bracket means the note was cut by a stop label
        # ("October 18, 2024 (Early access events at Spiel Essen from
        # October 3-6") -- the half-note is dropped.
        if date.count("(") > date.count(")"):
            date = date[:date.rfind("(")].strip()
        return date
    # History Pack 1 Blitz Decks (debug, 12 Oct 2026): "Key Dates: European
    # territories release date February 24, 2023 Rest of world release date
    # April 7, 2023 Product Details ..." -- no colon after "release date".
    m = re.search(r"Key Dates\s*:?\s*(.*?)(?=\s*(?:Product Details|Product type|Set code|Cards in set|MSRP|Barcodes|$))",
                  text, re.IGNORECASE)
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
    # "Cards in set" must have its colon: "265 cards in set" also appears
    # INSIDE Product Configuration values and must not start a new field.
    r"(?P<label>[Cc]ards in set:|Set Composition|Rarity Distribution|Set contains|"
    r"(?:[Ss]et|SET|Product|Pack|Box set)\s+Configuration|"
    # History Pack 1 Blitz Decks (debug, 12 Oct 2026) has no Configuration
    # label -- its contents sit under "Product Details".
    r"Product Details)\s*:?"
)
# Text that ends a field's value. Anything after these is page furniture.
_FIELD_STOP_RE = re.compile(
    r"\s*(?:\bStated card drop|\bNo specific card|(?i:\bResources\b)|\bCredits\b|\bMarketing Assets\b|"
    r"\bProduct Sheet\b|\bHow to Play\b|\bArmory Deck Reveal\b|\bUpgrade Your Deck\b|\bBooster Packs\b|"
    r"\bFind an Armory\b|\bLearn to Play\b|\bMSRP\b|\bBarcodes:|\bSKU:|\bDesigned (?:for|as|to)\b|\bDue to\b|"
    # Faith & Torment (debug, 12 Oct 2026): an event list follows the card
    # list -- "Where to Find It PAX Australia — October 9-11 ...". It pushed
    # the line past the length guard, so contents came back empty.
    r"\bWhere to Find It\b|"
    r"\bDeck List\b|\bPlease note\b|\bAvailable (?:free|languages)\b|\bUPP\b|\bMAP\b|\bLocal Play\b|"
    r"\bPreview Season\b|\bSpoilers\b|\bRelease Notes\b|\bOverview\b|\*Due|\*[A-Z]|"
    # Crucible of War 1st Ed: a buy-a-box promo paragraph follows the pack
    # configuration with no label in between.
    r"\bBuy-a-box\b)",
)
# Rarity names seen across all 44 listed products. A capitalised single
# word is also accepted when it is clearly a list item (followed by a comma,
# another count, or the end), so a new rarity name still parses -- but
# trailing prose like "Cold foil serialized hand drawn artist sketch cards"
# (Dusk till Dawn) does not get glued onto "10 Marvels".
_RARITY_NAME = (
    r"(?:Super\s+Rares?|Generic\s+Commons?|Class\s+Commons?|Pirate\s+Booty|Puzzle\s+Pieces?|"
    r"Fabled|Legendary|Majestics?|Rares?|Commons?|Tokens?|Basics?|Marvels?|Equipment|Promos?|"
    r"[A-Z][A-Za-z]+(?=\s*(?:,|\d|\?\?\?|$)))"
)
_RARITY_ITEM_RE = re.compile(r"(\d+|\?\?\?)\*{0,2}\s+(" + _RARITY_NAME + r")")
# One pull-rate slot ends with "... – <n> per <unit>" (+ optional note in
# brackets). Splitting AFTER that, rather than guessing where a slot name
# starts, keeps two-word names like "Cold Foil" / "Expansion Slot" whole.
_SLOT_END_RE = re.compile(
    r"(–\s*[\d+\- ]+\s*(?:per|in)\s+(?:\d+\s+)?(?:booster\s+)?(?:packs?|displays?|cases?|boxes?)(?:\s*\([^)]*\))?)\s+(?=[A-Z])"
)
_WORDS_BEFORE_COUNT_OK = {"containing", "contains", "contain", "of", "with", "plus", "and", "per", "to", "being",
                          "includes", "include", "is", "are"}  # "Each deck is 55 cards"
# Which "... Configuration" label wins when a page has several. fabtcg's
# Welcome to Rathe Unlimited page has a stray "set Configuration: 265 cards
# in set ..." copied from another product above its real Pack Configuration.
_CONFIG_PRIORITY = {"product configuration": 0, "pack configuration": 1, "box set configuration": 2,
                    "product details": 4}


def _clean_value(value):
    value = value.strip().lstrip(":").strip()
    # Arcane Rising 1st Ed puts a disclaimer INSIDE the rarity list:
    # "1 Fabled ( No specific card ... case. ) 14 Tokens ..." -- removed
    # before stop phrases are applied so the rest of the list survives.
    value = re.sub(r"\(\s*No specific card[^)]*\)", " ", value)
    # Rosetta Blitz Deck Collection opens its configuration with
    # "Designed for casual play 4 Rosetta Blitz Decks ...".
    value = re.sub(r"^Designed for [a-z ,/]+?play\s*", "", value)
    # Light & Shadow: "... (Rainbow Foil Marvel alternate art) Light & Shadow
    # was available at selected conventions in 2025. Conventions"
    value = re.sub(r"\s+[A-Z][^.;()]{0,40}?\bwas available\b.*$", "", value)
    m = _FIELD_STOP_RE.search(value, 1)
    if m:
        value = value[:m.start()]
    # Drop a dangling "EN"/"JP" that belongs to the NEXT label.
    value = re.sub(r"\s+(?:EN|JP)\s*$", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().rstrip(".,;:(").strip()


def _split_items(value):
    """'12 Mastery Pack Guardian booster packs 1 40-card Event Deck 6 Hero
    cards' -> '12 Mastery Pack Guardian booster packs; 1 40-card Event Deck;
    6 Hero cards'. Splits only where a new counted item starts (a number
    right after a word or ')'), never inside "each containing 40 cards" or
    before a "40-card" adjective."""
    value = _SLOT_END_RE.sub(r"\1; ", value)
    # Collector Series card lists: "Ser Boltyn, Breaker of Dawn (... art)
    # Vynnset, Iron Maiden (... art)" -> one card per item.
    value = re.sub(r"\)\s+(?=[A-Z])", "); ", value)
    # Silver Age chapters: "... basic cards Each deck comes with ..."
    value = re.sub(r"(?<=[a-z)])\s+(?=Each\s)", "; ", value)
    # "10 cards per booster pack Common – 8 per pack" (History Pack 1)
    value = re.sub(r"(per (?:booster )?pack)\s+(?=[A-Z])", r"\1; ", value)
    # "8 Rainbow Foil equipment cards An illustrated mini lore booklet A
    # quickstart playmat" (Classic Battles)
    value = re.sub(r"(?<=[a-z])\s+(?=An? [a-z])", "; ", value)
    # "12 decks per case 60-card Classic Constructed deck" (Armory Decks)
    value = re.sub(r"(per (?:case|display|carton))\s+(?=\d)", r"\1; ", value)
    out = []
    tokens = value.split(" ")
    for i, tok in enumerate(tokens):
        starts_item = (
            i > 0
            and re.match(r"^\d+\*{0,2}$", tok)
            and re.search(r"[A-Za-z)\]*]$", tokens[i - 1])
            and tokens[i - 1].lower().strip(",:") not in _WORDS_BEFORE_COUNT_OK
            # "booster pack of Set 18" -- a set number, not a new item. Capital
            # "Set" only: "265 cards in set 16 cards per pack" must still split.
            and tokens[i - 1] != "Set"
        )
        if starts_item:
            out.append("; ")
        elif i > 0:
            out.append(" ")
        out.append(tok)
    return re.sub(r";\s*;", ";", "".join(out))


def _format_rarity_list(value):
    """'1 Fabled 6* Legendary 46 Majestic' -> '1 Fabled, 6 Legendary, 46
    Majestic'. Falls back to the cleaned text if it doesn't look like a
    count list."""
    value = re.sub(r"\([^)]*\)", " ", value)
    # (no backslash inside the f-string -- that's a SyntaxError before Python 3.12)
    items = [n + " " + " ".join(name.split()) for n, name in _RARITY_ITEM_RE.findall(value)]
    return ", ".join(items) if len(items) >= 2 else value.strip()


def _format_distribution(value):
    """'Rainbow Foil – 1 per pack Rare or higher – 2 per pack (...)' ->
    one slot per item, separated by '; '."""
    return _SLOT_END_RE.sub(r"\1; ", value).strip()


def extract_contents(content_html):
    """Returns a list of short labelled lines: card count, rarity
    breakdown, pack/product configuration and pull rates -- only the ones
    the page actually states."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content_html, flags=re.DOTALL | re.IGNORECASE)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    text = _first_overview_block(re.sub(r"\s+", " ", text))

    labels = list(_FIELD_LABEL_RE.finditer(text))
    fields = {}
    config_candidates = []
    for i, m in enumerate(labels):
        end = labels[i + 1].start() if i + 1 < len(labels) else len(text)
        raw = text[m.end():end]
        label = re.sub(r"\s+", " ", m.group("label").lower().rstrip(":"))
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
            # A plain count list of rarities is a rarity breakdown; anything
            # describing packs/decks is configuration.
            looks_like_pack = re.search(
                r"per (?:pack|display|case|deck)|booster|deck|contains|cards in set|playmat",
                value, re.IGNORECASE,
            )
            if label == "product details" and re.fullmatch(r"\d+ card set", value):
                continue  # "536 card set" (HP2 Black Label) -- already the card count
            if label == "set configuration" and not looks_like_pack:
                fields.setdefault("rarity", _format_rarity_list(value))
            else:
                prio = _CONFIG_PRIORITY.get(label, 3)
                config_candidates.append((prio, -len(value), value))

    if config_candidates:
        # Best label first; among equals (Armory Deck: Legends lists the
        # same label twice, the first one cut short) the fuller one wins.
        fields["config"] = _split_items(sorted(config_candidates)[0][2])

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
    # A plain number searches by the product's WP id (the "SKU" in the
    # output), so one of several same-named products ("Silver Age") can be
    # picked without the interactive prompt.
    by_id = [p for p in products if str(p["id"]).lower() == search_term.strip().lower()]
    if by_id:
        return by_id  # also matches split chapter products by SKU, e.g. "FAB2603"
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
    release_date = extract_release_date(product["content_html"]) or product["listed_date"]
    contents = extract_contents(product["content_html"])

    return make_product_record(
        game="Flesh and Blood",
        title=product["title"],
        # WP post ID (NOT a merchant SKU) -- except for products split out of a
        # chapter hub page, which use the real "FAB2602"-style SKU the page states.
        sku=product["id"],
        release_date=release_date,
        image_url=product["image_url"],
        contents=contents,
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
    no_image, no_contents, count_mismatch = [], [], []
    for p in products:
        record = build_record(p)
        print_product_record(record)
        records.append(record)
        if not p["image_url"]:
            no_image.append(p)
        lines = extract_contents(p["content_html"])
        if not lines:
            no_contents.append(p)
        # fabtcg's own pages sometimes contradict themselves (Bright Lights
        # says "Cards in set: 251" and "265 cards in set") -- flagged, not
        # silently picked between.
        counts = set(re.findall(r"(?:Cards in set: |Configuration: )(\d+)\*{0,2}(?: cards in set|$)", "\n".join(lines), re.MULTILINE))
        if len(counts) > 1:
            count_mismatch.append((p, sorted(counts)))

    print(f"\n{len(no_image)} product(s) with no image found:")
    for p in no_image:
        print(f"  - {p['title']}  {p['source_url']}")
    def _looks_off(p):
        fname = p["image_url"].rsplit("/", 1)[-1].lower()
        return (not p["image_mentions_product"]) or any(w in fname for w in _BAD_IMG_WORDS)
    unsure = [p for p in products if p["image_url"] and _looks_off(p)]
    print(f"\n{len(unsure)} product(s) whose image may be wrong -- filename doesn't name the product, "
          f"or looks like a logo/banner/background (eyeball these):")
    for p in unsure:
        print(f"  - {p['title']}  -> {p['image_url'].rsplit('/', 1)[-1]}  ({p['image_source']})")
    no_date = [p for p in products if not p["has_page_date"]]
    print(f"\n{len(no_date)} product(s) with no release date on the page (showing the site's publish date instead):")
    for p in no_date:
        print(f"  - {p['title']}  (published {p['listed_date']})  {p['source_url']}")
    print(f"\n{len(no_contents)} product(s) with no card count / configuration found:")
    for p in no_contents:
        print(f"  - {p['title']}  {p['source_url']}")
    if count_mismatch:
        print(f"\n{len(count_mismatch)} product(s) whose page states two different card counts (check by hand):")
        for p, counts in count_mismatch:
            print(f"  - {p['title']}: {' vs '.join(counts)}  {p['source_url']}")
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
    if _DEBUG:
        print("\n[DEBUG] image candidates (score, source, url) -- highest wins:")
        for score, source, url in match["image_candidates"]:
            print(f"  {score:>4}  {source:<8} {url}")
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", match["content_html"]))
        print("\n[DEBUG] page text the contents parser sees:")
        print(re.sub(r"\s+", " ", text).strip())
    return record


if __name__ == "__main__":
    # A CLI arg still works (python discovery_fab.py "Outsiders") and skips
    # the prompt entirely -- use this if you're redirecting output to a file
    # (python discovery_fab.py "" > out.txt won't work for "all", but running
    # with no args and piping blank input in will, e.g. echo. | python ...).
    if len(sys.argv) >= 2:
        if sys.argv[1].strip().lower() == "all":
            discover_all()
        else:
            discover(sys.argv[1])
    else:
        search_term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        if search_term:
            discover(search_term)
        else:
            discover_all()