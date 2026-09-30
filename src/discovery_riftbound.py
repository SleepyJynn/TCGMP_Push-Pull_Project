"""
Discovery script for Riftbound sealed product data (Riot merch store).

Confirmed working sources (29 Sep 2026):
    Catalog: https://merch.riotgames.com/productPreviews/en-us.json.gz
    Product page: https://merch.riotgames.com/en-us/product/<slug>/

Scope settled (29 Sep 2026): pull EVERY official physical Riftbound product
from the catalog (boosters, decks, sleeves, playmats, everything) in one
pass, the same "pull ALL" scope as One Piece — no more single-search-term-
only CLI. Digital-only items (Portal Icon, Emote) are excluded per explicit
instruction: they're League client cosmetics, not physical TCG product.

Key differences from the One Piece source, confirmed via live diagnostic
dumps the user ran (see inspect_riftbound_catalog.py / inspect_riftbound_dates.py)
and real product-page View Source the user pasted:

  - The catalog is a dict keyed by SKU, so there is no pagination and no
    duplicate risk the way there was for One Piece's paginated listing.
  - There is NO structured category field (no equivalent of One Piece's
    data-cat). Category is inferred from title keywords instead — a weaker
    signal, but confirmed (29 Sep 2026) against a live run: all 28 real
    physical items classify correctly, none fall through to "Other".
  - There is NO genuine release-date field anywhere, on the catalog or on
    the product page itself: `drops_at` (catalog) / `dropAt` (page) is
    null/undefined for every item with no exception. The old version's
    workaround of scraping Wikipedia for a date near the search term was
    fragile, didn't scale to "pull everything", and its accuracy was never
    verified — it's been removed. Instead we use the catalog's `_createdAt`
    field directly (confirmed to match the same value exposed on the
    product page itself), labelled honestly as a "listed_date" rather than
    a claimed release date, since that's what it actually is.
  - There is NO structured Contents/spec table like One Piece's
    div.detailColStatus dt/dd pairs — only free-text marketing prose, e.g.
    "Each booster display contains 24 booster packs." or "Ready to Play -
    2 full 56-card preconstructed decks... Includes 2 full-size paper
    playmats and 2 booster packs for customization." This varies in
    wording per product type with no consistent structure, so rather than
    force it into a fabricated structured contents list (which would be
    guesswork for anything that isn't a plain booster display), we store
    the raw description text as-is. This was an explicit decision, not a
    default.
  - That description text is NOT in the page's visible DOM at all (the
    page is entirely client-rendered, Next.js/React Server Components) --
    it's a lazy-loaded reference (e.g. "copy": "$18") into the RSC data
    stream embedded in self.__next_f.push(...) script tags.
    extract_description() below resolves that reference properly. An
    earlier version instead grepped the raw page text for runs of <p>
    tags, which worked by coincidence for boosters/decks but silently
    corrupted the output for sleeves/playmats (leaked raw trailing JSON
    into the Contents field) — caught via a live run and fixed by
    verifying byte-for-byte against the real saved HTML before shipping.

Usage:
    python src/discovery_riftbound.py                  (pulls every product)
    python src/discovery_riftbound.py "Vendetta"        (search within all products)
"""

import gzip
import html as html_lib  # aliased -- "html" is already used as the page-source parameter name throughout this file
import json
import re
import sys
import requests
from common import make_product_record, print_product_record

CATALOG_URL = "https://merch.riotgames.com/productPreviews/en-us.json.gz"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
})

# Title-keyword -> category. Checked in order, first match wins, so put more
# specific phrases before more generic ones (e.g. "Showdown Deck" before
# "Deck"). NEEDS VERIFICATION against the real 28-item title list before
# treating this as final — flag back any title that falls through to "Other".
_CATEGORY_KEYWORDS = [
    ("Booster Display", ["booster display"]),
    ("Booster Pack", ["booster pack"]),
    ("Showdown Deck", ["showdown deck", "showdown"]),
    ("Champion Deck", ["champion deck"]),
    ("Starter Deck", ["starter deck"]),
    ("Signature Edition", ["signature edition"]),
    ("Box Set", ["box set"]),
    ("Bundle", ["bundle"]),
    ("Vault", ["vault"]),
    ("Playmat", ["playmat"]),
    ("Sleeves", ["sleeve"]),
    ("Proving Grounds", ["proving grounds"]),
    ("Deck", ["deck"]),
]


def classify_category(title):
    title_lower = (title or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(kw in title_lower for kw in keywords):
            return category
    return "Other"


def fetch_catalog():
    resp = session.get(CATALOG_URL, timeout=20)
    resp.raise_for_status()
    return json.loads(gzip.decompress(resp.content))


def fetch_product_page_html(slug):
    url = f"https://merch.riotgames.com/en-us/product/{slug}/"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


_PUSH_RE = re.compile(r'self\.__next_f\.push\((\[1,"(?:[^"\\]|\\.)*"\])\)')


def _decode_next_f_chunks(html):
    """Each self.__next_f.push([1, "..."]) call's argument is a valid JSON
    array literal: [1, "<JSON-escaped string>"]. json.loads on the whole
    array gives the real decoded text (quotes, unicode, HTML tags and all)
    -- this is what a first version of this function got wrong by hand-
    replacing \\u003c/\\u003e and quote escapes itself, which drifted past
    the real content boundary for some product types (see extract_description
    docstring)."""
    chunks = []
    for m in _PUSH_RE.finditer(html):
        try:
            _, text = json.loads(m.group(1))
            chunks.append(text)
        except (json.JSONDecodeError, ValueError):
            continue
    return chunks


def _resolve_next_f_ref(full, full_bytes, ref_id):
    """Resolve a React Flight lazy reference like "$18" against the
    concatenated, decoded push stream. Large string values are framed as
    rows of the form '<id>:T<hexlen>,<text>' where hexlen is the payload's
    length in UTF-8 BYTES (not characters, and not newline-terminated) --
    slicing by character count instead overshoots whenever the text has a
    multi-byte character (an em dash, a curly apostrophe) and leaks the
    start of the next row into the result. Confirmed against real
    Booster Display and Showdown Deck page source (29 Sep 2026): fixing
    this byte-vs-character mismatch is what stopped a trailing
    '7:["$",...' JSON fragment from being appended to the description."""
    m = re.search(rf'(?:^|\n){re.escape(ref_id)}:T([0-9a-fA-F]+),', full)
    if m:
        length = int(m.group(1), 16)
        byte_start = len(full[:m.end()].encode("utf-8"))
        return full_bytes[byte_start:byte_start + length].decode("utf-8", errors="replace")
    # Fallback for a non-text ("id:{...}" / "id:[...]") row: no declared
    # length, so cut at the next row boundary instead.
    m2 = re.search(rf'(?:^|\n){re.escape(ref_id)}:(.*)', full, re.DOTALL)
    if not m2:
        return None
    rest = m2.group(1)
    end = re.search(r'\n[0-9a-fA-F]+:', rest)
    return rest[:end.start()] if end else rest


def extract_description(html):
    """Pull the real marketing description off a Riftbound product page.

    Confirmed (29 Sep 2026) against real View Source for a Booster Display
    and a Showdown Deck: the page is entirely client-rendered (Next.js),
    so the description text isn't in the visible DOM at all. It lives in
    the product's "info" array as {"tabLabel": "...description", "copy":
    "$18"} -- "copy" is a React Flight lazy reference (a string like
    "$18"), not the text itself, which is why an earlier version that
    grepped for runs of <p> tags directly on the page sometimes grabbed
    the wrong span entirely (it worked by coincidence for boosters/decks,
    but corrupted sleeves/playmats by picking up an unrelated <p> run and
    running past it into raw JSON). This resolves the reference properly
    against the decoded RSC stream instead.

    Returns None if no "copy" field/reference is found (i.e. the page
    structure has changed and this needs re-checking against real HTML).
    """
    full = "".join(_decode_next_f_chunks(html))
    full_bytes = full.encode("utf-8")

    candidates = []
    for cm in re.finditer(r'"copy"\s*:\s*"(\$[0-9a-zA-Z]+|(?:[^"\\]|\\.)*)"', full):
        val = cm.group(1)
        if val.startswith("$"):
            text = _resolve_next_f_ref(full, full_bytes, val[1:])
            if text is None:
                continue
        else:
            try:
                text = json.loads('"' + val + '"')
            except (json.JSONDecodeError, ValueError):
                text = val
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_lib.unescape(text)  # &amp; / &#x27; / &nbsp; etc. -- confirmed
        # (30 Sep 2026) via a live run these show up un-decoded otherwise,
        # e.g. "Evelynn &amp; Seraphine".
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            candidates.append(text)

    if not candidates:
        return None
    # A product page can carry more than one "copy" field (e.g. a
    # companion-product tile); the real product's own description is
    # reliably the longest one.
    return max(candidates, key=len)


def get_all_riftbound_products():
    """Pull every physical Riftbound product from the catalog. Excludes the
    two digital-only items (Portal Icon, Emote) per explicit instruction --
    product_type == "digital" is the confirmed signal for those, checked
    against a full field dump of all 30 Riftbound-branded catalog entries.
    No dedup needed: the catalog is a dict keyed by SKU, so it's already
    unique."""
    data = fetch_catalog()
    riftbound_items = {
        k: v for k, v in data.items()
        if (v.get("brand") or {}).get("slug") == "riftbound"
    }
    physical_items = {
        k: v for k, v in riftbound_items.items()
        if v.get("product_type") != "digital"
    }

    products = []
    for sku, item in physical_items.items():
        slug = item.get("slug")
        products.append({
            "sku": sku,
            "title": item.get("title"),
            "category": classify_category(item.get("title")),
            "price": item.get("price"),
            "listed_date": item.get("_createdAt"),  # site listing date, NOT a confirmed retail release date -- see module docstring
            "image_url": (item.get("main_image") or {}).get("src"),
            "slug": slug,
            "source_url": f"https://merch.riotgames.com/en-us/product/{slug}/" if slug else None,
        })
    return products


def search_products(products, search_term):
    search_norm = search_term.lower()
    return [p for p in products if search_norm in (p["title"] or "").lower()]


def resolve_match(matches, search_term):
    if len(matches) == 1:
        return matches[0]

    search_norm = search_term.lower()
    exact = [m for m in matches if (m["title"] or "").lower() == search_norm]
    if len(exact) == 1:
        return exact[0]

    print(f"\n'{search_term}' matched {len(matches)} products — which one did you mean?")
    for i, m in enumerate(matches, start=1):
        print(f"  {i}. [{m['category']}] {m['title']}")
    choice = input(f"Enter number (1-{len(matches)}), or blank to cancel: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(matches)):
        print("Cancelled.")
        return None
    return matches[int(choice) - 1]


def build_record(product):
    description = None
    try:
        if product.get("slug"):
            page_html = fetch_product_page_html(product["slug"])
            description = extract_description(page_html)
    except requests.exceptions.RequestException as e:
        print(f"  (couldn't fetch page for {product.get('slug')}: {e})")

    return make_product_record(
        game="Riftbound",
        title=product["title"],
        sku=product["sku"],
        release_date=product.get("listed_date"),
        image_url=product.get("image_url"),
        contents=[description] if description else [],
        pack_configuration=[],
        price=product.get("price"),
        source_url=product.get("source_url"),
    )


def discover_all():
    products = get_all_riftbound_products()
    print(f"[DEBUG] Found {len(products)} physical Riftbound products (digital items excluded):")
    for p in products:
        print(f"  - [{p['category']}] '{p['title']}'  (SKU {p['sku']}, listed {p['listed_date']})")

    records = []
    for p in products:
        record = build_record(p)
        print_product_record(record)
        records.append(record)
    return records


def discover(search_term):
    products = get_all_riftbound_products()
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
    # A CLI arg still works (python discovery_riftbound.py "Vendetta") for
    # scripting, but matching the One Piece script's interactive style:
    # normally you just run it with no args and it prompts instead of
    # needing a quoted search term on the command line. Leaving the prompt
    # blank pulls every product, same as running with no args did before.
    if len(sys.argv) >= 2:
        discover(sys.argv[1])
    else:
        search_term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        if search_term:
            discover(search_term)
        else:
            discover_all()