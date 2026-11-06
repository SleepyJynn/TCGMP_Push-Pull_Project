"""
Discovery script for Cardfight!! Vanguard (Bushiroad) products, EN and JP,
all years, accessories included (scope agreed 2 Nov 2026).

Sources (official):
  EN  https://en.cf-vanguard.com   JP  https://cf-vanguard.com
  - The full product URL list comes from each site's product sitemap,
    /wp-sitemap-posts-products-1.xml (the sites have no product data feed).
    Confirmed 4 Nov 2026 (vanguard_diag2.py): EN 285 URLs, JP 1,582
    (trial 137, booster 171, supply 1,242, plus 32 "flat" URLs).
  - Each product page gives the code (【VGE-DZ-BT14】), name, release date,
    card types and pack contents. EN example:
      BOOSTER PACK 【VGE-BT01】 Booster Pack Vol.01: ... Release Date December
      10, 2011 Featured Clans ... Card Types 80 types of cards (RRR: 8/ ...)
      Others 1 pack contains 5 random cards 1 box contains 30 packs ...
  - JP flat URLs (/products/exist/, /products/g4_cam/ ...) are category,
    campaign and index pages, not products: skipped (listed in the summary).

Dates: exact dates are used as given. Some pages (mostly EN playmats) only
give a month ("June 2024"): the release date is left empty and the note says
"month only". Products releasing after today are skipped.

JP has ~1,550 product pages, so a full run takes a while. Pages already read
are cached in vanguard_cache.json next to the script's working folder, so a
second run is fast; delete that file to re-read everything.

Usage:
    python src/discovery_vanguard.py all --out=vanguard_output.txt
    python src/discovery_vanguard.py all --lang=en --out=vanguard_en.txt
    python src/discovery_vanguard.py "BT01" --debug --out=vanguard_debug.txt
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
_LANGS = ["en", "jp"]
if any(a.startswith("--lang=") for a in sys.argv):
    _lang_arg = next(a for a in sys.argv if a.startswith("--lang="))
    sys.argv.remove(_lang_arg)
    _LANGS = [_lang_arg.split("=", 1)[1].lower()]

import html as html_lib
import json
import os
import re
import time
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from common import make_product_record, print_product_record

SITES = {"en": "https://en.cf-vanguard.com", "jp": "https://cf-vanguard.com"}
CACHE_FILE = "vanguard_cache.json"
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
})
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
# h_about_bg / bana_: site-wide background and recruitment banner (141 JP products got them in the v2 run)
_BAD_IMG = re.compile(r"og-image|twitter|facebook|instagram|banner|thumbnail|footer|logo|icon|/themes/|h_about_bg|bana_", re.I)


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


CACHE_VERSION = 3   # bump when the parsing changes, so cached pages are re-read


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("_version") == CACHE_VERSION else {"_version": CACHE_VERSION}
    except (OSError, ValueError):
        return {"_version": CACHE_VERSION}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def slim_page(page, lang):
    """What the parsers need from a page (title, og:image, upload images, the
    product text), small enough to cache all ~1,800 pages. Cached slim pages
    are re-parsed on every run, so parsing fixes apply without re-reading."""
    title = re.search(r"<title>.*?</title>", page, re.S)
    og = re.search(r'<meta[^>]*property="og:image"[^>]*>', page)
    imgs = re.findall(r'(https?://[^"\'\s()<>]+/wp-content/uploads/[^"\'\s()<>]+\.(?:png|jpe?g|webp))', page)
    return ((title.group(0) if title else "") + (og.group(0) if og else "")
            + "".join(f'<img src="{u}">' for u in dict.fromkeys(imgs))
            + "<p>" + html_lib.escape(main_text(page, lang)) + "</p>")


def product_urls(lang):
    base = SITES[lang]
    index = _get(base + "/wp-sitemap.xml").text
    maps = [urljoin(base, u) for u in re.findall(r"<loc>(.*?)</loc>", index) if "posts-products" in u]
    urls = []
    for m in maps:
        for u in re.findall(r"<loc>(.*?)</loc>", _get(m).text):
            u = urljoin(base, html_lib.unescape(u))
            if u not in urls:
                urls.append(u)
    return urls


def is_listing_url(lang, url):
    """JP 'flat' URLs (/products/exist/, /products/minisleeve1/ ...) and the
    category roots (/products/trial/) are index/campaign pages."""
    if lang != "jp":
        return False
    parts = url.replace(SITES[lang], "").strip("/").split("/")
    return len(parts) < 3


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------
def page_image(page):
    og = re.search(r'property="og:image"[^>]*content="([^"]+)"', page)
    if og and not _BAD_IMG.search(og.group(1)):
        return og.group(1)
    for u in re.findall(r'(https?://[^"\'\s()<>]+/wp-content/uploads/[^"\'\s()<>]+\.(?:png|jpe?g|webp))', page):
        if not _BAD_IMG.search(u):
            return u
    return None


def main_text(page, lang):
    text = clean(re.sub(r"<(script|style|header|footer|nav)[^>]*>.*?</\1>", " ", page, flags=re.S))
    if lang == "en":
        start = text.find("Products >")
        end = text.find("Back to All Products")
    else:
        # FIXED 4 Nov 2026 (first JP run, 1,259 of 1,399 had no date): JP pages
        # start with a long prefecture menu, so the 8,000-char window ended
        # before the product details. Start at the product's own name instead.
        title = re.search(r"<title>(.*?)</title>", page, re.S)
        name = clean(title.group(1)).split("｜")[0].strip() if title else ""
        start = max(text.find("商品情報 >"), text.find("商品情報 ＞"))
        if name:
            k = text.find(name, max(start, 0) + 1)
            if k == -1:
                k = text.rfind(name)
            if k > start:
                start = k
        end = -1
        for marker in ("商品一覧へ戻る", "一覧へ戻る", "商品情報一覧", "TOPへ戻る"):
            j = text.find(marker, start if start > -1 else 0)
            if j != -1:
                end = j
                break
    if start == -1:
        start = 0
    return text[start:end if end != -1 else start + 12000]


def parse_en(page, url):
    t = main_text(page, "en")
    code = re.search(r"【([A-Z0-9\-]+)】", t)
    cat = re.search(r"([A-Z][A-Z ]{2,30})\s*【", t) or re.search(r"\b(BOOSTER PACK|CONSTRUCTED DECK|TRIAL DECK|OTHERS|SUPPLY)\b", t)
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    name = clean(title.group(1)).split("｜")[0].strip() if title else ""
    m = re.search(rf"Release Date\s+((?:{_MONTHS})(?:\s+\d{{1,2}}(?:st|nd|rd|th)?,?)?\s+\d{{4}})", t)
    raw_date = m.group(1) if m else ""
    iso = None
    if raw_date:
        d = re.match(rf"({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", raw_date)
        if d:
            iso = datetime.strptime(f"{d.group(1)} {d.group(2)} {d.group(3)}", "%B %d %Y").date().isoformat()
    lines = []
    ct = re.search(r"Card Types\s+(.+?)(?=\s(?:Others|Campaign|Price)\s|$)", t)
    if ct:
        types = ct.group(1).strip()
        decklist = re.findall(r"\b(\d+)pc\s", types)
        if len(decklist) >= 4:
            lines.append(f"Card types: decklist of {len(decklist)} card types, {sum(map(int, decklist))} cards")
        else:
            lines.append("Card types: " + types[:300])
    others = re.search(r"Others\s+(.+?)(?=\s(?:Campaign|Price)\s|$)", t)
    o = others.group(1) if others else t
    pack = re.search(r"1 pack contains (\d+)\s*(?:random\s*)?cards?", o, re.I)
    box = re.search(r"1 (?:box|display) contains (\d+) packs", o, re.I)
    if pack or box:
        bits = ([f"{pack.group(1)} cards per pack"] if pack else []) + ([f"{box.group(1)} packs per box"] if box else [])
        lines.append("Pack configuration: " + "; ".join(bits))
    if not ct:
        anyct = re.search(r"(\d+ types of cards[^.]*?\)(?:\s*\+\s*[^.]*?\))?)", t)
        if anyct:
            lines.append("Card types: " + anyct.group(1)[:300])
    pre = re.search(r"1 pre-constructed (?:\w+ )?deck contains (\d+)[^/]*?cards", o, re.I)
    if pre:
        lines.append(f"Includes: 1 pre-constructed deck of {pre.group(1)} cards")
    disp = re.search(r"1 display contains (\d+) decks", o, re.I)
    if disp:
        lines.append(f"Display: {disp.group(1)} decks per display")
    size = re.search(r"Size:\s*([\d. x]+cm)", o)
    if size:
        lines.append(f"Size: {size.group(1).strip()}")
    sets = re.search(r"(\d+) cards in this set", o)
    if sets:
        lines.append(f"Includes: {sets.group(1)} cards")
    inc = re.search(r"1 set contains:?\s*(.+?)(?=\s[A-Z][a-z]+ [a-z]+ |\s※|$)", o)
    if inc:
        items = [i.strip() for i in inc.group(1).split("・") if i.strip()]
        if items:
            lines.append("Includes: " + "; ".join(items)[:400])
    if not lines:
        after = t.split(raw_date, 1)[1] if raw_date and raw_date in t else t
        first = re.split(r"(?<=[.!?])\s", after.strip())[0]
        if len(first) > 20:
            lines.append("Description: " + first[:300])
    return {"code": code.group(1) if code else None, "category": cat.group(1).strip().title() if cat else None,
            "name": name, "release_date": iso, "release_raw": raw_date, "contents": lines,
            "image": page_image(page), "_text": t}


def parse_jp(page, url):
    t = main_text(page, "jp")
    code = re.search(r"【([A-Z][A-Z0-9\-]+)】", t) or re.search(r"(?:商品番号|品番)[：:\s]*([A-Z][A-Z0-9\-]+)", t)
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    name = clean(title.group(1)).split("｜")[0].split(" ｜")[0].strip() if title else ""
    m = re.search(r"発売日\s*[：:]?\s*(\d{4})年\s*(\d{1,2})月(?:\s*(\d{1,2})日)?", t) or \
        re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*[（(]?[月火水木金土日]?[）)]?\s*発売", t) or \
        re.search(r"発売日\s*[：:]?\s*(\d{4})[./](\d{1,2})[./](\d{1,2})", t) or \
        re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", t)
    iso, raw_date = None, ""
    if m:
        raw_date = m.group(0)
        if m.group(3):
            iso = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    lines = []
    kinds = re.search(r"(?:全|カード)\s*(\d+)\s*種", t)
    if kinds:
        lines.append(f"Card types: {kinds.group(1)} types")
    pack = re.search(r"1\s*パック\s*[（(]?\s*(\d+)\s*枚入り", t)
    box = re.search(r"1\s*(?:BOX|ボックス|箱)\s*[（(]?\s*(\d+)\s*パック入り", t)
    supply = "/supply/" in url
    if (pack or box) and not supply:
        bits = ([f"{pack.group(1)} cards per pack"] if pack else []) + ([f"{box.group(1)} packs per box"] if box else [])
        lines.append("Pack configuration: " + "; ".join(bits))
    elif supply and (pack or re.search(r"スリーブ\s*\d+\s*枚", t)):
        n_ = pack.group(1) if pack else re.search(r"スリーブ\s*(\d+)\s*枚", t).group(1)
        size = re.search(r"サイズ\s*[：:]\s*([^\s※／]+)", t)
        lines.append((f"Includes: {n_} sleeves per pack" if re.search(r"スリーブ", t[:300]) else f"Includes: {n_} per pack")
                     + (f" (size {size.group(1)})" if size else ""))
    # "セット内容 ・構築済みデッキ…77枚（メインデッキ：50枚、...） ・ショートストレイジボックス…1個"
    setc = re.search(r"(?:セット内容|内容)\s*((?:・[^・※]+){1,12})", t)
    if setc:
        items = [re.split(r"\s(?=\S*[！。!])", re.sub(r"\s+", " ", i).strip())[0]
                 for i in setc.group(1).split("・") if i.strip()]
        items = [i[:120] for i in items if re.search(r"\d", i)]
        if items:
            lines.append("Includes: " + "; ".join(items))
    deck = re.search(r"(?:構築済みデッキ|デッキ)\s*[：:]?\s*(\d+)\s*枚", t)
    if deck and not setc:
        lines.append(f"Includes: {deck.group(1)}-card deck")
    sleeves = re.search(r"(\d+)\s*枚入り", t) or re.search(r"スリーブ\s*(\d+)\s*枚", t)
    if not lines and sleeves:
        size = re.search(r"サイズ\s*[：:]\s*([^\s※／]+)", t)
        lines.append(f"Includes: {sleeves.group(1)} per pack" + (f" (size {size.group(1)})" if size else ""))
    if not lines:
        size = re.search(r"サイズ\s*[：:]\s*([^\s※／]+)", t)
        if size:
            lines.append(f"Size: {size.group(1)}")
    if not lines:
        after = t.split(raw_date, 1)[1] if raw_date and raw_date in t else t
        first = re.split(r"(?<=[。！!])", after.strip())[0]
        if len(first) > 10:
            lines.append("Description: " + first[:300])
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    group = url.replace(SITES["jp"], "").strip("/").split("/")[1] if url.count("/") > 4 else ""
    return {"code": code.group(1) if code else None,
            "category": {"booster": "Booster", "trial": "Constructed Deck", "supply": "Supply"}.get(group, group or None),
            "name": name, "release_date": iso, "release_raw": raw_date, "contents": lines,
            "image": page_image(page), "_text": t, "slug": slug}


# ---------------------------------------------------------------------------
def get_all_products():
    cache = load_cache()
    products, skipped, excluded, problems = [], [], [], []
    today = date.today().isoformat()
    for lang in _LANGS:
        urls = product_urls(lang)
        print(f"[{lang.upper()}] {len(urls)} product URLs in the sitemap", file=sys.__stdout__)
        for n, url in enumerate(urls, 1):
            if is_listing_url(lang, url) or url.rstrip("/").endswith("/schedule"):
                excluded.append((lang, url, "category / campaign / index page, not a product"))
                continue
            if url in cache:
                page = cache[url]
            else:
                if n % 50 == 0:
                    print(f"  [{lang.upper()}] reading page {n}/{len(urls)}...", file=sys.__stdout__)
                try:
                    page = _get(url).text
                except requests.exceptions.RequestException as e:
                    problems.append((lang, url, f"could not fetch: {e}"))
                    continue
                page = slim_page(page, lang)
                cache[url] = page
                if n % 100 == 0:
                    save_cache(cache)
                time.sleep(0.2)
            p = (parse_en if lang == "en" else parse_jp)(page, url)
            p.pop("_text", None)
            p = dict(p, lang=lang, url=url)
            if not p["name"]:
                problems.append((lang, url, "page has no product name"))
                continue
            if p["release_date"] and p["release_date"] > today:
                skipped.append((lang, p["name"], f"releases {p['release_date']}"))
                continue
            if not p["release_date"] and p["release_raw"]:
                mm = re.match(rf"({_MONTHS})\s+(\d{{4}})", p["release_raw"]) or \
                     re.match(r"(\d{4})年\s*(\d{1,2})月", p["release_raw"])
                if mm:
                    try:
                        ym = (datetime.strptime(f"{mm.group(1)} {mm.group(2)}", "%B %Y").strftime("%Y-%m")
                              if lang == "en" else f"{mm.group(1)}-{int(mm.group(2)):02d}")
                    except ValueError:
                        ym = None
                    if ym and ym > today[:7]:
                        skipped.append((lang, p["name"], f"releases {p['release_raw']} (month only)"))
                        continue
            products.append(p)
    save_cache(cache)
    seen, unique = set(), []
    for p in products:
        key = (p["lang"], p["code"] or re.sub(r"\W", "", p["name"].lower()), p["release_date"])
        # FIXED 4 Nov 2026: base and PREMIUM decksets (EN) and many JP sleeves
        # share a code, so only an identical name + code + date is a duplicate.
        key = key + (p["name"],)
        if key in seen:
            excluded.append((p["lang"], p["name"], "duplicate page (same name, code and date)"))
            continue
        seen.add(key)
        unique.append(p)
    unique.sort(key=lambda p: (p["lang"], p["release_date"] or "9999", p["name"]))
    return unique, skipped, excluded, problems


def build_record(p):
    return make_product_record(
        game=f"Cardfight!! Vanguard ({p['lang'].upper()})",
        title=p["name"],
        sku=p["code"],
        release_date=p["release_date"],
        image_url=p["image"],
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["url"],
    )


def print_notes(p):
    if p["release_date"]:
        print(f'Release note: on the product page: "{p["release_raw"]}"')
    elif p["release_raw"]:
        print(f'Release note: month only on the product page: "{p["release_raw"]}" (no exact day published)')
    else:
        print("Release note: NO DATE on the product page")
    if not p["image"]:
        print("Image note: no product photo found on the page")


def discover_all():
    products, skipped, excluded, problems = get_all_products()
    for lang in _LANGS:
        mine = [p for p in products if p["lang"] == lang]
        print(f"[DEBUG] Found {len(mine)} Cardfight!! Vanguard {lang.upper()} products")
    for p in products:
        print_product_record(build_record(p))
        print(f"Category: {p['category']}")
        print_notes(p)
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for lang, n, why in skipped:
        print(f"  - [{lang.upper()}] {n}: {why}")
    print(f"\n{len(excluded)} URL(s) left out on purpose:")
    for lang, n, why in excluded:
        print(f"  - [{lang.upper()}] {n}: {why}")
    print(f"\n{len(problems)} problem(s):")
    for lang, n, why in problems:
        print(f"  - [{lang.upper()}] {n}: {why}")
    for label, test in (("no exact release date", lambda p: not p["release_date"]),
                        ("no image", lambda p: not p["image"]), ("no contents", lambda p: not p["contents"])):
        bad = [p for p in products if test(p)]
        print(f"\n{len(bad)} product(s) with {label}:")
        for p in bad[:300]:
            print(f"  - [{p['lang'].upper()}] {p['name']}  {p['url']}")
        if len(bad) > 300:
            print(f"  ... and {len(bad) - 300} more")


def discover(term):
    products, skipped, excluded, problems = get_all_products()
    terms = [x.strip().lower() for x in term.split("|") if x.strip()]
    matches = [p for p in products if any(x in (p["name"] + " " + (p["code"] or "")).lower() for x in terms)]
    if not matches:
        print(f"No product found matching '{term}'")
        for lang, n, why in skipped:
            if any(x in n.lower() for x in terms):
                print(f"  ([{lang.upper()}] {n}: {why})")
    for p in matches[:40]:
        print_product_record(build_record(p))
        print(f"Category: {p['category']}")
        print_notes(p)
        if _DEBUG:
            page = _get(p["url"]).text
            print(f"[DEBUG] page text used:\n  {main_text(page, p['lang'])[:2500]}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() != "all":
        discover(sys.argv[1])
    elif len(sys.argv) >= 2:
        discover_all()
    else:
        term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        discover(term) if term else discover_all()