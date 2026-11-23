"""
Discovery script for CookieRun: Braverse TCG (Devsisters) products, ENGLISH.

Source: the official site, https://cookierunbraverse.com
  - The site is a Nuxt app. Its product pages (/en/product/BOOSTERPACK,
    /en/product/detail?id=N) load everything from one JSON file:
        https://cookierunbraverse.com/data/json/product_en.json
    (found in the site's JavaScript with cookierun_diag2.py, 11 Nov 2026).
    { categoryList: [...], productList: [ {product_idx, category_title,
      product_name, product_thumbnail_image, product_featured_image,
      product_release_date, product_price, product_component (HTML contents),
      product_enable, delete_dt, expire_dt, ...} ] }
  - Korean products are in product.json; this script is EN only (scope agreed
    11 Nov 2026).
Release dates: as written on the site. Some EN products only give a month
("October, 2026") or nothing; exact dates from other sources are filled in
from _DATE_FALLBACK (with the source), the rest are left without a date and
listed at the end.
Accessories included. Products releasing after today are skipped (for a
month-only date: when that month has not started yet).

Usage:
    python src/discovery_cookierun.py all --out=cookierun_output.txt
    python src/discovery_cookierun.py "Brave Beginning|Starter" --debug --out=cookierun_debug.txt
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

import requests
from common import make_product_record, print_product_record

BASE = "https://cookierunbraverse.com"
DATA_URL = BASE + "/data/json/product_en.json"
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": BASE + "/en/product/BOOSTERPACK",
})
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"

# Exact dates the official site doesn't give (it shows only a month, or nothing).
# Researched 13 Nov 2026; only confirmed exact dates are used here.
_ICV2_LAUNCH = ("2025-07-11", "ICv2: 'CookieRun TCG will hit stores on July 11, 2025' "
                "(https://icv2.com/print/article/59724)")
_DATE_FALLBACK = {
    "Brave Beginning": _ICV2_LAUNCH,
    "CookieRun Card Game Starter Deck Blue": _ICV2_LAUNCH,
    "CookieRun Card Game Starter Deck Green": _ICV2_LAUNCH,
    "CookieRun Card Game Starter Deck Purple": _ICV2_LAUNCH,
    "CookieRun Card Game Starter Deck Red": _ICV2_LAUNCH,
    "CookieRun Card Game Starter Deck Yellow": _ICV2_LAUNCH,
    "Legend of the Spirit Weapons": ("2027-01-22", "ICv2: 'into retail on January 22, 2027' "
                                     "(https://icv2.com/articles/news/view/63327)"),
}


def _get_json(url, attempts=3):
    for i in range(attempts):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.RequestException, ValueError):
            if i == attempts - 1:
                raise
            time.sleep(2 * (i + 1))


def parse_date(text):
    """-> (iso date or None, 'YYYY-MM' if month-only, note)"""
    t = (text or "").strip()
    m = re.search(rf"({_MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})", t)
    if m:
        d = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
        return d.isoformat(), None, f'official site: "{t}"'
    m = re.search(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), None, f'official site: "{t}"'
        except ValueError:
            pass
    m = re.search(rf"({_MONTHS})\s*,?\s*(\d{{4}})", t)
    if m:
        mo = datetime.strptime(m.group(1), "%B").month
        return None, f"{m.group(2)}-{mo:02d}", f'month only on the official site: "{t}"'
    return None, None, f'no date on the official site: "{t}"' if t else "no date on the official site"


def good_image(url):
    """A real product image URL, or None for blanks and the site's logo placeholder."""
    u = (url or "").strip()
    if not u.startswith("http") or "logo-og" in u or "/manager/assets/" in u:
        return None
    return u


def contents_from(component):
    """product_component HTML -> one line per <p>/<li>, empty lines dropped."""
    s = component or ""
    s = re.sub(r"<\s*(?:br|/p|/li|/div|/h\d)\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<\s*li[^>]*>", "\n• ", s, flags=re.I)
    s = html_lib.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\\n", "\n")
    lines = []
    for line in s.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line and line != "•":
            lines.append(line)
    return lines


def get_all_products():
    data = _get_json(DATA_URL)
    today = date.today()
    this_month = f"{today.year}-{today.month:02d}"
    products, skipped, excluded = [], [], []
    for it in data.get("productList") or []:
        name = html_lib.unescape((it.get("product_name") or "").strip())
        if str(it.get("product_enable", "1")) != "1" or it.get("delete_dt"):
            excluded.append((name, "hidden or deleted on the site"))
            continue
        rel, month, how = parse_date(it.get("product_release_date"))
        if not rel and name in _DATE_FALLBACK:
            rel, src = _DATE_FALLBACK[name]
            how = f"researched release date ({src}); official site shows " + (
                f'"{it.get("product_release_date")}"' if it.get("product_release_date") else "no date")
            month = None
        if (rel and rel > today.isoformat()) or (month and month > this_month):
            skipped.append((name, f"releases {rel or month}"))
            continue
        featured, thumb = good_image(it.get("product_featured_image")), good_image(it.get("product_thumbnail_image"))
        image = featured or thumb
        products.append({
            "name": name,
            "idx": it.get("product_idx"),
            "category": it.get("category_title") or it.get("category_slug") or "",
            "release_date": rel,
            "release_month": month,
            "release_how": how,
            "image": image,
            "image_note": None if featured else ("list thumbnail (no usable featured image)" if image else
                                                 "no product image on the site (only a placeholder)"),
            "contents": contents_from(it.get("product_component")),
            "price": (it.get("product_price") or "").strip(),
            "shop_link": (it.get("product_link") or "").strip(),
            "source_url": f"{BASE}/en/product/detail?id={it.get('product_idx')}",
            "_raw": it,
        })
    unique, keys = [], set()
    for p in products:
        k = (re.sub(r"[^a-z0-9]", "", p["name"].lower()), p["release_date"] or p["release_month"])
        if k in keys:
            continue
        keys.add(k)
        unique.append(p)
    unique.sort(key=lambda p: (p["release_date"] or (p["release_month"] or "9999") + "-99", p["name"]))
    get_all_products.categories = [c.get("category_title") for c in data.get("categoryList") or []]
    return unique, skipped, excluded


def build_record(p):
    return make_product_record(
        game="CookieRun: Braverse",
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
    print(f"Category: {p['category']}" + (f" | Price: {p['price']}" if p["price"] else ""))
    print(f"Release note: {p['release_how']}" if p["release_date"] else f"Release note: NO EXACT DATE -- {p['release_how']}")
    if p["image_note"]:
        print(f"Image note: {p['image_note']}")


def discover_all():
    products, skipped, excluded = get_all_products()
    cats = {}
    for p in products:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
    print(f"[DEBUG] Found {len(products)} CookieRun: Braverse EN products: "
          + ", ".join(f"{k} {v}" for k, v in sorted(cats.items())))
    print(f"[DEBUG] site categories: {getattr(get_all_products, 'categories', [])}")
    for p in products:
        print_product_record(build_record(p))
        print_notes(p)
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for n, why in skipped:
        print(f"  - {n}: {why}")
    print(f"\n{len(excluded)} item(s) left out (hidden/deleted on the site):")
    for n, why in excluded:
        print(f"  - {n}: {why}")
    for label, test in (("no exact release date", lambda p: not p["release_date"]),
                        ("no image", lambda p: not p["image"]),
                        ("no contents", lambda p: not p["contents"])):
        bad = [p for p in products if test(p)]
        print(f"\n{len(bad)} product(s) with {label}:")
        for p in bad:
            extra = p["release_how"] if label.startswith("no exact") else p["source_url"]
            print(f"  - {p['name']}  {extra}")


def discover(term):
    products, skipped, _ = get_all_products()
    terms = [t.strip().lower() for t in term.split("|") if t.strip()]
    matches = [p for p in products if any(t in p["name"].lower() for t in terms)]
    if not matches:
        print(f"No product found matching '{term}'")
        for n, why in skipped:
            if any(t in n.lower() for t in terms):
                print(f"  (skipped as not released yet: {n}, {why})")
    for p in matches:
        print_product_record(build_record(p))
        print_notes(p)
        if _DEBUG:
            raw = {k: (str(v)[:400] if v is not None else None) for k, v in p["_raw"].items()}
            print("[DEBUG] raw JSON fields:")
            for k, v in raw.items():
                print(f"  {k}: {v}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() != "all":
        discover(sys.argv[1])
    elif len(sys.argv) >= 2:
        discover_all()
    else:
        term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        discover(term) if term else discover_all()