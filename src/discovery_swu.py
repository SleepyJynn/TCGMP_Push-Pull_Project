"""
Discovery script for Star Wars: Unlimited (Fantasy Flight Games) sealed products.

Source: the official site, https://starwarsunlimited.com
  - The full list of product pages comes from the site's own content API:
    https://admin.starwarsunlimited.com/api/product-pages  (slug per page),
    confirmed by the 22 Oct 2026 diagnostic run (swu_diag.py).
  - Each page, /products/<slug>, is a Next.js page whose __NEXT_DATA__ JSON
    holds the page's real data: props.pageProps.product.attributes has
    the set name, its releaseDate ("2026-07-17") and the page blocks.
    The products are "blocks.featured-products" entries: title1..3,
    description1..3, image1..3 (cdn.starwarsunlimited.com). Entries with a
    button to a non-product page (Product Guide, Rules, Event Locator) are
    site links, not products.
  - Single-product pages (Intro Battle: Hoth) have no featured products; the
    page itself is the product.

Scope (agreed 22 Oct 2026):
  - All sealed products on the official site. Accessories EXCLUDED (the
    official site lists none; Asmodee's store sells them but has no release
    dates).
  - Not-yet-released products are skipped (same rule as Lorcana / GA) and are
    picked up automatically once their release date has passed.
  - Contents missing from the official page (Spotlight Deck booster pack,
    booster display pack counts, Intro Battle: Hoth box contents) are filled
    in from Asmodee's own product pages (FFG's parent company), each with a
    source note, and only when the official page doesn't state them.

Release dates: the set's releaseDate from the page data. The Prerelease Box
page text says "This product launches one week before the rest of the set",
so its date is the set date minus 7 days (the note says so).

Usage:
    python src/discovery_swu.py all --out=swu_output.txt
    python src/discovery_swu.py "Homeworlds" --debug --out=swu_debug.txt
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

_DEBUG = "--debug" in sys.argv
if _DEBUG:
    sys.argv.remove("--debug")

import html as html_lib
import json
import re
import time
from datetime import date, timedelta

import requests
from common import make_product_record, print_product_record

BASE = "https://starwarsunlimited.com"
API = "https://admin.starwarsunlimited.com/api"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
})

# Featured-product entries that are site links, not products.
_NAV_TITLES = {"product guide", "rules", "event locator", "how to play", "card database", "deck builder"}


def _is_nav_url(url):
    u = (url or "").lower()
    return u.startswith(("/product-guide", "/how-to-play", "/cards", "/deck-builder")) or "type=events" in u

# ---------------------------------------------------------------------------
# Contents NOT stated on starwarsunlimited.com, from Asmodee's own product
# pages (Asmodee owns Fantasy Flight Games). Checked 22 Oct 2026. Used only
# when the official page doesn't already state the same thing.
# ---------------------------------------------------------------------------
_SPOTLIGHT_EXTRA = (
    "1 {set} booster pack",
    "store.asmodee.com product pages for the Ashes of the Empire (Luke Skywalker) and Homeworlds "
    "(Chewbacca) Spotlight Decks: 'One pre-built 50-card deck and one Ashes of the Empire booster "
    "pack' / '50-card fixed deck, 1 base card, 1 leader card, 3 double-sided token cards, 31 counters, "
    "1 Homeworlds Booster Pack' (same product format for every set's Spotlight Decks)",
)
_BOOSTER_DISPLAY_EXTRA = (
    "24 booster packs per booster display",
    "https://store.asmodee.com/products/star-wars-unlimited-secrets-of-power-booster-display "
    "('you'll find 24 individually wrapped booster packs')",
)
_CARBONITE_DISPLAY_EXTRA = (
    "12 Carbonite Edition booster packs per booster display",
    "https://shop.asmodee.ca/en/ffgswh0606en ('you'll find 12 individually wrapped Carbonite booster packs')",
)
# Whole-product fallbacks for single-product pages (keyed by page slug).
_PAGE_FALLBACK = {
    "intro-battle-hoth": {
        "contents": ["Includes: 2 pre-built decks (Darth Vader's Imperial army, Leia Organa's Rebel forces); "
                     "104 cards; 6 reference cards; 1 game board; 1 info sheet; 1 rulebook; "
                     "57 damage counters; 2 epic action counters; 1 initiative counter"],
        "contents_source": "https://www.asmodee.ca/en/product/star-wars-unlimited-intro-battle-hoth/ "
                           "('What's in the box'); the two decks are named on the official page",
        "image": "https://cdn.svc.asmodee.net/production-asmodeeca/uploads/image-converter/2025/09/"
                 "FFGVSWHA002EN-STAR_WARS-UNLIMITED-INTRO_BATTLE_OF_HOTH-EN-3D_LEFT-1024x1024.jpg",
        "image_source": "Asmodee Canada product page (the official page has no box photo, only key art "
                        "and gameplay shots)",
    },
}


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def _get(url, attempts=3):
    for i in range(attempts):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException:
            if i == attempts - 1:
                raise
            time.sleep(2 * (i + 1))


def clean(fragment):
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def fetch_slugs():
    """Every product page slug, from the site's content API (paged)."""
    slugs, page = [], 1
    while True:
        url = f"{API}/product-pages?locale=en&pagination[pageSize]=100&pagination[page]={page}"
        data = _get(url).json()
        for row in data.get("data") or []:
            slug = (row.get("attributes") or {}).get("slug")
            if slug and slug not in slugs:
                slugs.append(slug)
        meta = (data.get("meta") or {}).get("pagination") or {}
        if page >= (meta.get("pageCount") or 1):
            break
        page += 1
    return slugs


def page_data(slug):
    """props.pageProps.product.attributes of a product page."""
    page = _get(f"{BASE}/products/{slug}").text
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return None
    props = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    product = props.get("product") or {}
    return product.get("attributes") if isinstance(product, dict) else None


def _img(field):
    data = (field or {}).get("data") if isinstance(field, dict) else None
    if isinstance(data, list):
        data = data[0] if data else None
    return ((data or {}).get("attributes") or {}).get("url")


# ---------------------------------------------------------------------------
# Contents
# ---------------------------------------------------------------------------
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_NUM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"


def contents_from(desc, ptype):
    """Contents lines from an official product description."""
    text = clean(desc)
    lines = []
    # Booster packs: "In each pack, you'll find 16 cards ..., including approximately
    # 9 common cards, 3 uncommon cards, ... and 1 Hyperspace-foil card."
    m = re.search(rf"each pack,? you.ll find ({_NUM}) cards[^.]*?including (approximately )?([^.]+)\.", text, re.I)
    if m:
        n = m.group(1)
        n = _NUM_WORDS.get(n.lower(), n)
        parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+(?=\d)", m.group(3)) if p.strip()]
        approx = "approximately " if m.group(2) else ""
        lines.append(f"Pack configuration: {n} cards per pack ({approx}{'; '.join(parts)})")
    # Pre-built decks: "This pre-built, ready-to-play 50-card deck"
    d = re.search(r"(\d+)-card decks?", text)
    if d and ptype in ("Spotlight Deck", "Starter", "Deck"):
        lines.append(f"Includes: 1 pre-built {d.group(1)}-card deck")
    # Counted items: "Inside, you'll find 6 booster packs", "four Icons 2027 booster packs plus
    # an exclusive Cassian Andor variant card"
    if not lines:
        found = []
        for m in re.finditer(rf"\b({_NUM})\s+((?:[\w’'-]+\s){{0,4}}?(?:booster packs?|packs?|decks?|cards?))\b"
                             rf"(\s+plus\s+an?\s+[^.]+?card)?", text, re.I):
            ph = f"{m.group(1)} {m.group(2)}"
            if m.group(3):
                ph += "; 1 " + re.sub(r"^\s*plus\s+an?\s+", "", m.group(3), flags=re.I)
            # "four Icons booster packs" (slogan) and "four Icons 2027 booster packs"
            # are the same item: same count + same item word -> keep the longer.
            key = (m.group(1).lower(), m.group(2).split()[-1].lower())
            dup = next((j for j, f in enumerate(found)
                        if (f.split()[0].lower(), f.split(";")[0].split()[-1].lower()) == key), None)
            if dup is None:
                found.append(ph)
            elif len(ph) > len(found[dup]):
                found[dup] = ph
        if found:
            lines.append("Includes: " + "; ".join(found))
    if not lines and text:
        sents = re.split(r"(?<=[.!?])\s", text)
        # Skip a slogan opener ("Enhance your collection with ... packs!") when
        # a real descriptive sentence follows.
        first = next((s for s in sents if not s.endswith("!")), sents[0])
        if len(first) >= 40:
            lines.append("Description: " + first[:300])
    return lines


def classify(title):
    t = title.lower()
    if "prerelease" in t:
        return "Prerelease Box"
    if "carbonite" in t:
        return "Carbonite Edition Booster Packs"
    if "spotlight deck" in t:
        return "Spotlight Deck"
    if "4-pack" in t or "card 4" in t:
        return "Booster 4-Pack"
    if "booster" in t:
        return "Booster Packs"
    if "starter" in t:
        return "Starter"
    if "deck" in t:
        return "Deck"
    return "Other"


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
def products_from_page(slug, attrs):
    prod = ((attrs.get("product") or {}).get("data") or {}).get("attributes") or {}
    set_name = prod.get("name") or slug
    release = prod.get("releaseDate")
    products = []
    for block in attrs.get("content") or []:
        if block.get("__component") != "blocks.featured-products":
            continue
        for i in range(1, 10):
            title = (block.get(f"title{i}") or "").strip()
            if not title:
                continue
            button = block.get(f"button{i}") or {}
            url = (button.get("url") or "") if isinstance(button, dict) else ""
            # FIXED 22 Oct 2026 (first live run): sets 1-7 have "Buy Now" buttons
            # pointing to /search (the store locator), so "button not to /products"
            # wrongly dropped every product there. Only real site-navigation
            # entries (Product Guide, Rules, Event Locator) are skipped now.
            if title.lower() in _NAV_TITLES or _is_nav_url(url):
                continue
            ptype = classify(title)
            name = title if set_name.lower() in title.lower() else f"{set_name} {title}"
            products.append({"name": name, "type": ptype, "set_name": set_name, "slug": slug,
                             "image": _img(block.get(f"image{i}")),
                             "description": block.get(f"description{i}") or "",
                             "set_release": release})

    if not products and not slug.startswith("set-"):
        # A page for one product (Intro Battle: Hoth): the page itself is it.
        text = " ".join(clean(b.get("description")) for b in attrs.get("content") or []
                        if b.get("__component") in ("blocks.image-and-text", "blocks.cta"))
        products.append({"name": set_name, "type": classify(set_name), "set_name": set_name, "slug": slug,
                         "image": None, "description": text, "set_release": release, "single": True,
                         "key_art": _img(prod.get("mainImage"))})

    for p in products:
        p["contents"] = [] if p.get("single") else contents_from(p["description"], p["type"])
        p["contents_sources"] = []
        p["release_date"], p["release_how"] = release, "set release date on the official page"
        if p["type"] == "Prerelease Box" and release and re.search(r"one week before", clean(p["description"]), re.I):
            p["release_date"] = (date.fromisoformat(release) - timedelta(days=7)).isoformat()
            p["release_how"] = (f"official page: 'launches one week before the rest of the set' "
                                f"(set releases {release})")
        _add_extras(p)
    return products


def _add_extras(p):
    joined = " ".join(p["contents"]).lower()
    if p.get("single"):
        fb = _PAGE_FALLBACK.get(p["slug"])
        if fb:
            p["contents"], p["image"] = list(fb["contents"]), fb["image"]
            p["contents_sources"].append(fb["contents_source"])
            p["image_source"] = fb["image_source"]
        else:
            p["contents"] = contents_from(p["description"], p["type"])
            if not p["image"] and p.get("key_art"):
                p["image"], p["image_source"] = p["key_art"], "page header key art (no box photo on the page)"
        return
    if p["type"] == "Spotlight Deck" and "booster pack" not in joined:
        line, src = _SPOTLIGHT_EXTRA
        p["contents"].append("Includes: " + line.format(set=p["set_name"]))
        p["contents_sources"].append(src)
    if p["type"] == "Booster Packs" and "16 cards per pack" in joined and "24" not in joined:
        line, src = _BOOSTER_DISPLAY_EXTRA
        p["contents"].append("Box configuration: " + line)
        p["contents_sources"].append(src)
    if p["type"] == "Carbonite Edition Booster Packs" and "12" not in joined:
        line, src = _CARBONITE_DISPLAY_EXTRA
        p["contents"].append("Box configuration: " + line)
        p["contents_sources"].append(src)


def get_all_products():
    slugs = fetch_slugs()
    all_products, skipped, problems = [], [], []
    today = date.today()
    for slug in slugs:
        try:
            attrs = page_data(slug)
        except requests.exceptions.RequestException as e:
            problems.append((slug, f"could not fetch: {e}"))
            continue
        if not attrs:
            problems.append((slug, "no page data (__NEXT_DATA__) found"))
            continue
        found = products_from_page(slug, attrs)
        if not found:
            comps = sorted({b.get("__component") for b in attrs.get("content") or []})
            problems.append((slug, f"no products found on the page (blocks: {comps})"))
        for p in found:
            if p["release_date"] and date.fromisoformat(p["release_date"]) > today:
                skipped.append((p["name"], f"releases {p['release_date']}"))
                continue
            p["source_url"] = f"{BASE}/products/{slug}"
            p["_attrs"] = attrs
            all_products.append(p)

    unique, seen = [], set()
    for p in all_products:
        key = re.sub(r"[^a-z0-9]", "", p["name"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    unique.sort(key=lambda p: (p["set_release"] or "9999", p["name"]))
    return unique, skipped, problems


def build_record(p):
    return make_product_record(
        game="Star Wars: Unlimited",
        title=p["name"],
        sku=None,
        release_date=p["release_date"],
        image_url=p["image"],
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["source_url"],
    )


def print_notes(p):
    if p["release_date"]:
        print(f"Release note: {p['release_how']}")
    else:
        print("Release note: NO DATE FOUND on the page")
    for src in p["contents_sources"]:
        print(f"Contents note: some contents not on the official page -- {src}")
    if p.get("image_source"):
        print(f"Image note: {p['image_source']}")
    elif not p["image"]:
        print("Image note: no image for this product on the page")


def discover_all():
    products, skipped, problems = get_all_products()
    print(f"[DEBUG] Found {len(products)} Star Wars: Unlimited products:")
    for p in products:
        print(f"  - [{p['type']}] {p['name']}  ({p['release_date']})")
    for p in products:
        print_product_record(build_record(p))
        print_notes(p)
    no_date = [p for p in products if not p["release_date"]]
    no_img = [p for p in products if not p["image"]]
    no_contents = [p for p in products if not p["contents"]]
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for n, why in skipped:
        print(f"  - {n}: {why}")
    print(f"\n{len(problems)} page(s) with a problem:")
    for slug, why in problems:
        print(f"  - {slug}: {why}")
    print(f"\n{len(no_date)} product(s) with no release date:")
    for p in no_date:
        print(f"  - {p['name']}  {p['source_url']}")
    print(f"\n{len(no_img)} product(s) with no image:")
    for p in no_img:
        print(f"  - {p['name']}  {p['source_url']}")
    print(f"\n{len(no_contents)} product(s) with no contents:")
    for p in no_contents:
        print(f"  - {p['name']}  {p['source_url']}")


def discover(term):
    products, skipped, problems = get_all_products()
    matches = [p for p in products if term.lower() in p["name"].lower()]
    if not matches:
        print(f"No product found matching '{term}'")
        for n, why in skipped:
            if term.lower() in n.lower():
                print(f"  (skipped as not released yet: {n}, {why})")
    for p in matches:
        print_product_record(build_record(p))
        print_notes(p)
        if _DEBUG:
            print("\n[DEBUG] official description:")
            print("  " + clean(p["description"])[:1500])
            print("[DEBUG] page blocks:")
            for b in p["_attrs"].get("content") or []:
                print(f"  {b.get('__component')}: {(b.get('title') or b.get('heading') or '')[:80]}")
                if b.get("__component") in ("blocks.featured-products", "blocks.set-details"):
                    for k, v in b.items():
                        if k.startswith(("title", "button", "description", "image")) and v:
                            val = _img(v) if k.startswith("image") else (v if isinstance(v, (str, dict)) else v)
                            print(f"      {k}: {clean(str(val))[:300]}")
                elif b.get("__component") in ("blocks.image-and-text", "blocks.cta", "blocks.wysiwyg"):
                    print(f"      text: {clean(b.get('description') or b.get('content'))[:400]}")
                    if b.get("image"):
                        print(f"      image: {_img(b.get('image'))}")
    if _DEBUG and problems:
        print("\n[DEBUG] pages with a problem:")
        for slug, why in problems:
            print(f"  - {slug}: {why}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() != "all":
        discover(sys.argv[1])
    elif len(sys.argv) >= 2:
        discover_all()
    else:
        term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        discover(term) if term else discover_all()