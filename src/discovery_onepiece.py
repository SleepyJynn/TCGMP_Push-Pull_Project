"""
Discovery script for One Piece Card Game sealed product data.

Confirmed working sources (22 Sep 2026):
    EN listing: https://en.onepiece-cardgame.com/products/?view=normal&subcategory=all&page=N
    JP listing: https://onepiece-cardgame.com/products/?view=normal&subcategory=all&page=N

Fix applied (21 Sep 2026): the listing is paginated (18+ pages across all
categories) and mixes boosters/decks/others together unless you pass a
subcategory. The previous version only ever fetched page 1 of the
unfiltered "all" listing, so most products (including newer ones further
back in the catalog) were never even loaded. It also filtered links by
checking "/products/boosters/" in the href, which wrongly excludes a few
boosters that live directly at /products/<code>.html instead (e.g. EB-05,
OP-16) — real category comes from the li's data-cat attribute, not the
URL shape.

Extended (22 Sep 2026): now pulls ALL sealed products (boosters, decks,
others), not just boosters, by hitting subcategory=all and reading each
item's own data-cat attribute (boosters / decks / others) instead of
filtering to one category. Same pagination, same per-item fields
(title, url, release_date, price, image_url) plus a new "category" field.

Scope settled (28 Sep 2026): pull EVERY official product, accessories
included — no "does it actually contain cards" filtering. An earlier pass
added a has-cards classifier for the "others" category, but that's no
longer needed: get_all_products() is the one entry point, and it already
returns every official product with no duplicates.

Search hardened (28 Sep 2026): normalize_text() now normalizes all the
smart/curly punctuation the site actually uses in titles (curly quotes, en
dash, em dash), not just the apostrophe — confirmed by running every real
product's own title back through search (see test_search_all_products.py).
discover() also no longer silently takes the first substring match when a
search term is ambiguous (e.g. "Official Playmat" matches 9 different real
products) — see resolve_match().

Contents parsing rewritten (29 Sep 2026): extract_contents_and_rarity() now
parses the real "Contents"/"Rarity" fields Bandai publishes per product
page (div.detailColStatus > dl > dt/dd), instead of a regex that only
caught the booster-style "X types in total" phrasing. This means decks,
double packs, and other non-booster products now actually return their
real printed contents (e.g. "Constructed Deck x 1 (51 cards), DON!! Card
x 10, ...") instead of silently coming back empty.

Usage:
    python src/discovery_onepiece.py
    (will prompt for region, then set name)
"""

import re
import sys
import requests
from bs4 import BeautifulSoup
from common import make_product_record, print_product_record

REGIONS = {
    "1": ("EN", "https://en.onepiece-cardgame.com"),
    "2": ("JP", "https://onepiece-cardgame.com"),
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})


def prompt_region():
    print("Which region?")
    for key, (label, _) in REGIONS.items():
        print(f"  {key}. {label}")
    choice = input("Enter number: ").strip()
    return REGIONS.get(choice)


def fetch_listing(base_url, subcategory="all", page=1):
    url = f"{base_url}/products/?view=normal&subcategory={subcategory}&page={page}"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def parse_product_listing(html, base_url, page=None):
    """Parse every product on the page, whatever category it's in. Category
    comes from the li's own data-cat attribute (boosters / decks / others),
    not the URL shape — some booster products (e.g. EB-05, OP-16) don't live
    under /products/boosters/ at all, they're at /products/eb05.html etc.,
    so a URL-based filter silently drops real products."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for li in soup.select("li.linkListColBox"):
        a = li.select_one("a.linkListColItem")
        if not a or not a.get("href"):
            continue

        href = a["href"]
        full_url = href if href.startswith("http") else base_url + href

        title_el = a.select_one("h4.linkListColTitle")
        title = title_el.get_text(strip=True) if title_el else a.get_text(" ", strip=True)

        time_el = a.select_one("p.linkListColDate time")
        release_date = time_el.get_text(strip=True) if time_el else None

        price_el = a.select_one("p.linkListColPrice span.data")
        price = price_el.get_text(strip=True) if price_el else None

        # Real per-product thumbnail lives right here in the listing, in
        # data-src (lazy-loaded). The og:image meta tag on the individual
        # product page turned out to just be the site-wide default ogp.png,
        # not a real per-product image, so use this instead.
        img_el = a.select_one("div.linkListColThumb img")
        image_url = None
        if img_el:
            raw_src = img_el.get("data-src") or img_el.get("src")
            if raw_src:
                image_url = raw_src if raw_src.startswith("http") else base_url + raw_src

        products.append({
            "title": title,
            "url": full_url,
            "category": li.get("data-cat"),  # "boosters" / "decks" / "others"
            "release_date": release_date,
            "price": price,
            "image_url": image_url,
            "page": page,  # which listing page this was found on (for debugging duplicates)
        })

    return products


def get_all_products(base_url, subcategory="all"):
    """Paginate through every page of a listing (site tells us the max via
    pageMax). subcategory="all" pulls boosters + decks + others in one
    paginated sweep; pass "boosters"/"decks"/"others" to narrow it.

    Confirmed (23 Sep 2026) via page-tracked test runs: the same item
    occasionally appears on two *adjacent* pages (e.g. page 6 and page 7) —
    a genuine pagination boundary overlap, not a site-wide double-listing
    (those instead show up as different titles sharing one product page,
    which is legitimate and must NOT be deduped). So: dedupe on
    (url, title) — same url + same title = a real duplicate to drop; same
    url + different title = a real distinct product, kept as-is.
    """
    page = 1
    all_products = []
    while True:
        html = fetch_listing(base_url, subcategory=subcategory, page=page)
        all_products.extend(parse_product_listing(html, base_url, page=page))

        soup = BeautifulSoup(html, "html.parser")
        max_el = soup.select_one("span.pageMax")
        total_pages = int(max_el.get_text(strip=True)) if max_el and max_el.get_text(strip=True).isdigit() else 1
        if page >= total_pages:
            break
        page += 1

    seen = set()
    deduped = []
    for p in all_products:
        key = (p["url"], p["title"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    return deduped


def fetch_product_page_html(url):
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def extract_image(html, base_url):
    match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if match:
        url = match.group(1)
        return url if url.startswith("http") else base_url + url
    return None


def extract_contents_and_rarity(html):
    """Parse the real "Contents" / "Rarity" fields Bandai publishes per
    product page, inside div.detailColStatus > dl > dt/dd pairs.

    Confirmed (29 Sep 2026) against real View Source from two different
    product templates:
      - Starter deck (ST-31): Contents is a plain sentence in <dd><p>...
        e.g. "Constructed Deck x 1 (51 cards), DON!! Card x 10, Playsheet
        x 1, BOOSTER PACK -THE TIME OF BATTLE- [OP-16]" — split on commas.
      - Double Pack Set (DP-12): Contents is an itemized <dd><ul><li> list
        (each line prefixed with a full-width "・" bullet), e.g.
        "・Booster Pack [OP-17] x2" / "・DON!! Card x1 (2 types)".
    The old version only matched the booster-style "X types in total"
    phrasing via a whole-page regex, so it silently returned nothing for
    every deck/double-pack/accessory product (matches what discover() was
    printing as "Contents: (none found)" for those).
    """
    soup = BeautifulSoup(html, "html.parser")
    status = soup.select_one("div.detailColStatus")
    if not status:
        return [], []

    contents = []
    rarity = []
    for dl in status.select("dl"):
        dt = dl.select_one("dt")
        dd = dl.select_one("dd")
        if not dt or not dd:
            continue
        label = dt.get_text(strip=True)
        if label == "Contents":
            items = dd.select("li")
            if items:
                contents = [li.get_text(strip=True).lstrip("・").strip() for li in items]
            else:
                text = dd.get_text(" ", strip=True)
                if text:
                    contents = [part.strip() for part in text.split(",") if part.strip()]
        elif label == "Rarity":
            text = dd.get_text(" ", strip=True)
            if text:
                rarity = [part.strip() for part in text.split(",") if part.strip()]

    return contents, rarity


# Smart/curly punctuation the site uses in titles, mapped to the plain
# character a real user would type on a keyboard. Confirmed necessary by
# test_search_all_products.py against the live EN catalog (28 Sep 2026):
# titles like "Limited Card Sleeve \u2013Standard Purple Silver-" (en dash)
# and 'Playmat and Storage Box Set -Eustass\u201cCaptain\u201dKid-' (curly
# double quotes) weren't matched by a plain-keyboard-typed search until all
# of these were normalized, not just the apostrophe.
_PUNCTUATION_EQUIVALENTS = {
    "\u2019": "'",   # right single quote
    "\u2018": "'",   # left single quote
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u2013": "-",   # en dash
    "\u2014": "-",   # em dash
}


def normalize_text(s):
    for smart, plain in _PUNCTUATION_EQUIVALENTS.items():
        s = s.replace(smart, plain)
    return s.lower()


def search_products(products, search_term):
    """Substring search over a product list, case-insensitive and punctuation-
    normalized. Pulled out of discover() so it can be tested directly
    (see test_search_all_products.py) without going through stdin/stdout."""
    search_norm = normalize_text(search_term)
    return [p for p in products if search_norm in normalize_text(p["title"])]


def resolve_match(matches, search_term):
    """Pick one product out of possibly-several substring matches.

    Confirmed (28 Sep 2026) via test_search_all_products.py: short/generic
    search terms like "Official Playmat" or "Limited Card Sleeve" are
    genuine substrings of many real titles, so search_products() can return
    several hits. Silently taking the first one risks handing back the
    wrong product. Rule: if exactly one match's title equals the search
    term (case/punctuation-insensitive), use it outright. Otherwise, if
    there's more than one match, ask the user to pick from the list.
    Returns the chosen product dict, or None if the user backs out.
    """
    if len(matches) == 1:
        return matches[0]

    search_norm = normalize_text(search_term)
    exact = [m for m in matches if normalize_text(m["title"]) == search_norm]
    if len(exact) == 1:
        return exact[0]

    print(f"\n'{search_term}' matched {len(matches)} products — which one did you mean?")
    for i, m in enumerate(matches, start=1):
        print(f"  {i}. [{m['category']}] {m['title']}  ({m['release_date']})")
    choice = input(f"Enter number (1-{len(matches)}), or blank to cancel: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(matches)):
        print("Cancelled.")
        return None
    return matches[int(choice) - 1]


def discover(region_base_url, search_term):
    products = get_all_products(region_base_url, subcategory="all")

    print(f"[DEBUG] Found {len(products)} product items total (all categories):")
    for p in products:
        print(f"  - [{p['category']}] '{p['title']}'  ({p['release_date']})")

    matches = search_products(products, search_term)

    if not matches:
        print(f"No product found matching '{search_term}'")
        return

    match = resolve_match(matches, search_term)
    if match is None:
        return
    print(f"[matched] [{match['category']}] {match['title']}  ({match['url']})")

    # Prefer the real per-product thumbnail already captured from the
    # listing page; only fall back to the product page's og:image if the
    # listing somehow didn't have one (it's a generic site-wide image, so
    # it's a worse answer, not a better one).
    image_url = match.get("image_url")
    contents = []
    pack_config = []
    try:
        page_html = fetch_product_page_html(match["url"])
        if not image_url:
            image_url = extract_image(page_html, region_base_url)
        contents, pack_config = extract_contents_and_rarity(page_html)
    except requests.exceptions.RequestException as e:
        print(f"(Couldn't fetch product page: {e})")

    record = make_product_record(
        game="One Piece TCG",
        title=match["title"],
        sku=None,
        release_date=match["release_date"],
        image_url=image_url,
        price=match.get("price"),
        contents=contents,
        pack_configuration=pack_config,
        source_url=match["url"],
    )
    print_product_record(record)


if __name__ == "__main__":
    region_choice = prompt_region()
    if not region_choice:
        print("Invalid region selected.")
        sys.exit(1)
    region_label, region_base_url = region_choice

    search_term = input("Set name to search: ").strip().strip('"').strip("'")
    if not search_term:
        print("No search term given.")
        sys.exit(1)

    discover(region_base_url, search_term)