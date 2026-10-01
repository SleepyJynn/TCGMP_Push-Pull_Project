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
"""

import html as html_lib
import re
import sys
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
        resp = session.get(PRODUCT_URL, params={"per_page": 100, "page": page}, timeout=20)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_items.extend(batch)
        total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1

    products = []
    for item in all_items:
        title = html_lib.unescape(item.get("title", {}).get("rendered", ""))
        content_html = item.get("content", {}).get("rendered", "")
        if _is_junk_product(title, content_html):
            continue

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
            "needs_human_check": title.strip().lower() in _NO_COMMERCE_EXCEPTIONS,
        })
    return products


def extract_release_date(content_html):
    """Try confirmed real release-date phrasing on the page first; only
    14/90 products had this in a live check, so most products will fall
    back to listed_date instead (handled by the caller)."""
    text = re.sub(r"<[^>]+>", " ", content_html)
    text = html_lib.unescape(text)
    patterns = [
        r"Release date:\s*([A-Za-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})",
        r"In Stores\s*([A-Za-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


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
    description = extract_description(product["content_html"])
    release_date = extract_release_date(product["content_html"]) or product["listed_date"]

    return make_product_record(
        game="Flesh and Blood",
        title=product["title"],
        sku=product["id"],  # WP post ID, NOT a real merchant SKU/UPC -- none exposed by this API
        release_date=release_date,
        image_url=None,  # no reliable per-product image field confirmed yet in this API
        contents=[description] if description else [],
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
        records.append(record)
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
    return record


if __name__ == "__main__":
    # A CLI arg still works (python discovery_fab.py "Outsiders") and skips
    # the prompt entirely -- use this if you're redirecting output to a file
    # (python discovery_fab.py "" > out.txt won't work for "all", but running
    # with no args and piping blank input in will, e.g. echo. | python ...).
    if len(sys.argv) >= 2:
        discover(sys.argv[1])
    else:
        search_term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        if search_term:
            discover(search_term)
        else:
            discover_all()