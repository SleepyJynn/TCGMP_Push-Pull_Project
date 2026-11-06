"""
Discovery script for Sorcery: Contested Realm (Erik's Curiosa) sealed products.

Source: the official site, https://sorcerytcg.com
  - The footer "Product" menu links one page per release: /product/<slug>
    (frost-mage-vs-lavamancer, gothic, dragonlord, arthurian-legends, beta),
    confirmed by the 28 Oct 2026 diagnostic run (sorcery_diag.py).
  - Pages are Next.js (App Router, no __NEXT_DATA__), read from the rendered
    HTML. Each page has a hero subtitle with the date ("Released November
    2023", "Launching November 13 2026", "Out Now"), then product sections
    under <h2> headings ("Booster Box", "Elemental Precon Decks", "The
    Dragonlord Box"; Gothic puts three products under one "Products"
    heading). Each product's text ends with "SKU: <code>" (Dragonlord has
    no SKU).
  - Not on the site, so not pulled: Alpha (Kickstarter-only, 2023) and the
    shop's binders/apparel (shop.sorcerytcg.com returned no product list).

Release dates: the page date when it is a full date; otherwise (month-only
or "Out Now") the _RELEASE_DATES table below, each with its source.
Products releasing after today are skipped (same rule as the other games).

Usage:
    python src/discovery_sorcery.py all --out=sorcery_output.txt
    python src/discovery_sorcery.py "Gothic" --debug --out=sorcery_debug.txt
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
import re
import time
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import requests
from common import make_product_record, print_product_record

BASE = "https://sorcerytcg.com"
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
})

# Used when the page shows only a month or "Out Now". Checked 28 Oct 2026.
_RELEASE_DATES = {
    "beta": ("2023-11-10", "https://sorcerytcg.com/news/sorcery-contested-realm-beta-global-release-date "
                           "('rescheduling this to the following month, on November 10th')"),
    "arthurian-legends": ("2024-10-04", "https://sorcerytcg.com/news/arthurian-legends-everything-you-need-to-know-for-release "
                                        "('Arthurian Legends officially launches on October 4th!')"),
    "dragonlord": ("2025-08-02", "https://sorcerytcg.com/news/the-dragonlord-awakens-august-2 "
                                 "('Launching August 2 (yes, 2025)')"),
    "gothic": ("2025-12-05", "https://seller.tcgplayer.com/blog/introducing-gothic-sorcery-contested-realms-newest-set "
                             "('releases on December 5, 2025')"),
}
# Section headings that are not products.
_NOT_PRODUCT_H2 = re.compile(r"full card library|latest news|card library|news", re.I)
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


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


def strip_scripts(page):
    return re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)


def fetch_slugs():
    home = _get(BASE + "/").text
    slugs = []
    for m in re.finditer(r'href="(?:https://sorcerytcg\.com)?/product/([a-z0-9\-]+)"', home):
        if m.group(1) not in slugs:
            slugs.append(m.group(1))
    return slugs


def full_image_url(src):
    """/_next/image?url=%2F_next%2Fstatic%2Fmedia%2Fbox-beta.png&w=... -> https://sorcerytcg.com/_next/static/media/box-beta.png"""
    src = html_lib.unescape(src)
    if src.startswith("/_next/image"):
        inner = parse_qs(urlparse(src).query).get("url", [""])[0]
        src = inner or src
    return BASE + src if src.startswith("/") else src


def page_images(page):
    out = []
    for tag in re.findall(r"<img[^>]*>", page):
        src = re.search(r'src="([^"]+)"', tag)
        if not src:
            continue
        alt = re.search(r'alt="([^"]*)"', tag)
        out.append((full_image_url(src.group(1)), html_lib.unescape(alt.group(1)) if alt else ""))
    return out


# ---------------------------------------------------------------------------
# Page -> products
# ---------------------------------------------------------------------------
_NAME_RE = re.compile(
    r"(Booster Box|Pre-?constructed Decks\s*[\"“][^\"”]+[\"”]|Elemental Precon Decks|Precon(?:structed)? Decks"
    r"|Playmat\s*[\"“][^\"”]+[\"”]|The Dragonlord Box)", re.I)


def page_title_and_date(page):
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    title = clean(h1.group(1)) if h1 else None
    h3 = re.search(r"<h3[^>]*>(.*?)</h3>", page, re.S)
    return title, clean(h3.group(1)) if h3 else ""


def to_iso(text):
    m = re.search(rf"({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", text)
    if m:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
    return None


def product_sections(page):
    """[(h2 heading, text until the next h2)] for the product headings."""
    body = strip_scripts(page)
    heads = list(re.finditer(r"<h2[^>]*>(.*?)</h2>", body, re.S))
    out = []
    for i, h in enumerate(heads):
        title = clean(h.group(1))
        if _NOT_PRODUCT_H2.search(title):
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        text = clean(body[h.end():end])
        text = text.split("Next slide")[0].strip()
        out.append((title, text))
    return out


def split_items(text):
    """Split a section's text at each 'SKU: CODE'. Returns [(item text, sku)]."""
    items, pos = [], 0
    for m in re.finditer(r"SKU:\s*([A-Z0-9]+)", text):
        items.append((text[pos:m.start()].strip(), m.group(1)))
        pos = m.end()
    rest = text[pos:].strip()
    if rest and not items:
        items.append((rest, None))
    return items


def classify(name):
    n = name.lower()
    if "booster box" in n:
        return "Booster Box"
    if "precon" in n or "preconstructed" in n:
        return "Preconstructed Decks"
    if "playmat" in n:
        return "Playmat"
    if "dragonlord box" in n:
        return "Mini Set Box"
    if " vs" in n:
        return "Two-Player Deck Box"
    return "Other"


_NUM_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6"}


def contents_from(text, ptype):
    lines = []
    box = re.search(r"(\d+)\s+booster packs", text, re.I)
    pack = re.search(r"Each (\d+)-card booster contains (.+?)\.", text, re.I)
    if box:
        lines.append(f"Box configuration: {box.group(1)} booster packs per booster box")
    if pack:
        parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+(?=\d)", pack.group(2)) if p.strip()]
        lines.append(f"Pack configuration: {pack.group(1)} cards per pack ({'; '.join(parts)})")
        extra = re.search(r"(A card may be replaced with [^.]+\.)", text)
        if extra:
            lines.append("Pack note: " + extra.group(1))
    decks = re.search(r"\b(\w+) (\d+)[- ]card decks", text, re.I)
    if decks:
        n = _NUM_WORDS.get(decks.group(1).lower(), decks.group(1))
        lines.append(f"Includes: {n} preconstructed decks of {decks.group(2)} cards each")
    two = re.search(r"(two|\d+) ready-to-play decks[^,]*, each featuring ([^.]+)\.", text, re.I)
    if two:
        n = _NUM_WORDS.get(two.group(1).lower(), two.group(1))
        parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", two.group(2)) if p.strip()]
        lines.append(f"Includes: {n} ready-to-play decks, each with {'; '.join(parts)}")
    mini = re.search(r"Each (\d+)-card box contains (.+?)\.", text, re.I)
    if mini:
        parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+(?=\d)", mini.group(2)) if p.strip()]
        lines.append(f"Includes: {mini.group(1)} cards ({'; '.join(parts)})")
    mat = re.search(r"([\d ½¼¾.]+\"?\s*[×x]\s*[\d ½¼¾.]+\"?)\s*(neoprene)?\s*playmat", text, re.I)
    if ptype == "Playmat" and mat:
        lines.append(f"Includes: 1 {'neoprene ' if mat.group(2) else ''}playmat, {mat.group(1).strip()}")
    if not lines and text:
        first = re.split(r"(?<=[.!?])\s", text)[0]
        if len(first) >= 30:
            lines.append("Description: " + first[:300])
    return lines


def pick_image(images, ptype, set_name):
    """The product photo for a product type, by alt text."""
    want = {
        "Booster Box": r"booster box",
        "Preconstructed Decks": r"precon|preconstructed",
        "Mini Set Box": r"\bbox\b",
        "Two-Player Deck Box": r"\bbox\b",
        "Playmat": r"playmat",
    }.get(ptype)
    if want:
        for src, alt in images:
            if re.search(want, alt, re.I):
                return src, None
    for src, alt in images:   # one shared photo of all the set's products (Gothic)
        if re.search(r"\bproducts\b", alt, re.I):
            return src, "one shared official photo of all this set's products"
    return None, None


def products_from_page(slug, page):
    title, hero = page_title_and_date(page)
    set_name = title or slug.replace("-", " ").title()
    images = page_images(page)
    products = []
    for heading, text in product_sections(page):
        for item, sku in split_items(text):
            m = _NAME_RE.search(item)
            label = m.group(1) if m else heading
            if label.lower() == "products":
                continue
            label = label.replace("“", '"').replace("”", '"')
            norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
            name = label if norm(set_name) in norm(label) else f"{set_name} {label}"
            ptype = classify(label)
            desc = item[m.end():].lstrip(" –-") if m and m.group(1) != heading else item
            if ptype == "Booster Box" and m and m.start() > 0:
                desc = item   # Gothic: set intro + "Booster Box with 36 booster packs..."
            img, img_note = pick_image(images, ptype, set_name)
            products.append({"name": name, "type": ptype, "sku": sku, "set_name": set_name, "slug": slug,
                             "description": desc, "contents": contents_from(desc, ptype),
                             "image": img, "image_note": img_note})
    # Release date
    page_date = to_iso(hero)
    if page_date:
        release, how = page_date, f'on the product page: "{hero}"'
    elif slug in _RELEASE_DATES:
        release, src = _RELEASE_DATES[slug]
        how = f'page says "{hero}" -- exact date from {src}'
    else:
        release, how = None, f'page says "{hero}" (no exact date known)'
    for p in products:
        p["release_date"], p["release_how"] = release, how
    return products


def get_all_products():
    slugs = fetch_slugs()
    all_products, skipped, problems = [], [], []
    for slug in slugs:
        url = f"{BASE}/product/{slug}"
        try:
            page = _get(url).text
        except requests.exceptions.RequestException as e:
            problems.append((slug, f"could not fetch: {e}"))
            continue
        found = products_from_page(slug, page)
        if not found:
            problems.append((slug, "no products found on the page"))
        for p in found:
            if p["release_date"] and date.fromisoformat(p["release_date"]) > date.today():
                skipped.append((p["name"], f"releases {p['release_date']}"))
                continue
            p["source_url"] = url
            p["_page"] = page
            all_products.append(p)
    unique, seen = [], set()
    for p in all_products:
        key = p["sku"] or re.sub(r"[^a-z0-9]", "", p["name"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    unique.sort(key=lambda p: (p["release_date"] or "9999", p["name"]))
    return unique, skipped, problems


def build_record(p):
    return make_product_record(
        game="Sorcery: Contested Realm",
        title=p["name"],
        sku=p["sku"],
        release_date=p["release_date"],
        image_url=p["image"],
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["source_url"],
    )


def print_notes(p):
    print(f"Release note: {p['release_how']}" if p["release_date"] else f"Release note: NO DATE -- {p['release_how']}")
    if p.get("image_note"):
        print(f"Image note: {p['image_note']}")
    elif not p["image"]:
        print("Image note: no product photo found on the page")


def discover_all():
    products, skipped, problems = get_all_products()
    print(f"[DEBUG] Found {len(products)} Sorcery: Contested Realm products:")
    for p in products:
        print(f"  - [{p['type']}] {p['name']}  ({p['release_date']})")
    for p in products:
        print_product_record(build_record(p))
        print_notes(p)
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for n, why in skipped:
        print(f"  - {n}: {why}")
    print(f"\n{len(problems)} page(s) with a problem:")
    for s, why in problems:
        print(f"  - {s}: {why}")
    for label, test in (("no release date", lambda p: not p["release_date"]), ("no image", lambda p: not p["image"]),
                        ("no contents", lambda p: not p["contents"])):
        bad = [p for p in products if test(p)]
        print(f"\n{len(bad)} product(s) with {label}:")
        for p in bad:
            print(f"  - {p['name']}  {p['source_url']}")


def discover(term):
    products, skipped, problems = get_all_products()
    terms = [t.strip().lower() for t in term.split("|") if t.strip()]
    matches = [p for p in products if any(t in p["name"].lower() for t in terms)]
    if not matches:
        print(f"No product found matching '{term}'")
        for n, why in skipped:
            if any(t in n.lower() for t in terms):
                print(f"  (skipped as not released yet: {n}, {why})")
    shown_pages = set()
    for p in matches:
        print_product_record(build_record(p))
        print_notes(p)
        if _DEBUG:
            print(f"\n[DEBUG] text used for contents:\n  {p['description'][:1500]}")
            if p["slug"] not in shown_pages:
                shown_pages.add(p["slug"])
                print("[DEBUG] product sections on this page (h2 -> text):")
                for h, t in product_sections(p["_page"]):
                    print(f"  {h}  ->  {t[:600]}")
                print("[DEBUG] images (src | alt):")
                for s, a in page_images(p["_page"]):
                    if a:
                        print(f"  {s} | {a}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() != "all":
        discover(sys.argv[1])
    elif len(sys.argv) >= 2:
        discover_all()
    else:
        term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        discover(term) if term else discover_all()