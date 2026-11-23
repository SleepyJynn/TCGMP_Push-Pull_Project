"""
Discovery script for GUNDAM CARD GAME (Bandai) products, ENGLISH + JAPANESE.

Source: the official site, https://www.gundam-gcg.com
  - Product list: /en/products/list.php?page=N and /jp/products/list.php?page=N
    (12 products per page, ~5 pages each, checked 11 Nov 2026 with
    new_games_diag.py). Each card: category label (STARTER DECK / BOOSTER PACK /
    ACCESSORIES / PREMIUM BANDAI / OTHER; JP デッキ / ブースター / 周辺アイテム /
    プレミアムバンダイ / その他), name with [CODE], release date, MSRP, thumbnail,
    link to /<lang>/products/<code>.html.
  - Product page: Release Date / 発売日, MSRP, Contents (x1 ..., x10 ...),
    Rarity breakdown for boosters, Design Types for sleeves, main image.
EN dates look like "September 25,2026"; JP dates like "2027.4.17".
Accessories included. Products releasing after today are skipped.

Usage:
    python src/discovery_gundam.py all --out=gundam_output.txt
    python src/discovery_gundam.py all --lang=jp --out=gundam_jp.txt
    python src/discovery_gundam.py "ST13|GD05" --debug --out=gundam_debug.txt
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
import re
import time
from datetime import date, datetime
from urllib.parse import quote, urljoin

import requests
from common import make_product_record, print_product_record

BASE = "https://www.gundam-gcg.com"
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
})
_CATEGORIES = ["STARTER DECK", "BOOSTER PACK", "ACCESSORIES", "PREMIUM BANDAI", "OTHER",
               "デッキ", "ブースター", "周辺アイテム", "プレミアムバンダイ", "その他"]
_CAT_EN = {"デッキ": "STARTER DECK", "ブースター": "BOOSTER PACK", "周辺アイテム": "ACCESSORIES",
           "プレミアムバンダイ": "PREMIUM BANDAI", "その他": "OTHER"}
_PRODUCT_HREF = re.compile(r'<a[^>]+href="((?:https://www\.gundam-gcg\.com)?/(?:en|jp)/products/([a-z0-9\-]+)\.html)"', re.I)
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
# where the "Contents" text stops on a product page
_JUNK = (r"VIEW MORE PRODUCTS|参戦作品|商品一覧|Availability|入手方法|The illustration|写真と実際|RELATED|関連商品|"
         r"LEARN MORE|GUNDAM ASSEMBLE is here|本商品は|QUESTIONNAIRE|商品を見る|Product details are|※|"
         r"Due to the type of the product|Images may differ|PREMIUM BANDAI\(|Available in limited quantities|"
         r"What is \"|What is “")
_STOP = r"(?:" + _JUNK + r"|Rarity|収録|レアリティ|Design Types|デザイン|Featured|$)"


def _get(url, attempts=3):
    for i in range(attempts):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
            return r.text
        except requests.exceptions.RequestException:
            if i == attempts - 1:
                raise
            time.sleep(2 * (i + 1))


def clean(fragment):
    s = re.sub(r"<br\s*/?>", " ", fragment or "", flags=re.I)
    s = html_lib.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\u200b", "")
    return re.sub(r"\s+", " ", s).strip()


def strip_scripts(page):
    return re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)


def full_url(src, page_url):
    u = urljoin(page_url, html_lib.unescape(src.strip()))
    return quote(u, safe=":/%?=&#+,;@~!$'()*")


def to_iso(text):
    t = text or ""
    m = re.search(rf"({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})", t)
    if m:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
    m = re.search(r"(\d{4})\s*[./年]\s*(\d{1,2})\s*[./月]\s*(\d{1,2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# List pages
# ---------------------------------------------------------------------------
def list_cards(page, page_url):
    """[{url, slug, category, name, code, date_text, msrp, thumb}] from one list page."""
    body = strip_scripts(page)
    hits = list(_PRODUCT_HREF.finditer(body))
    cards = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(body)
        chunk = body[m.start():end]
        close = chunk.find("</a>")
        inner = chunk[:close] if close != -1 else chunk[:3000]
        txt = clean(inner)
        before = clean(body[max(0, m.start() - 400):m.start()])
        category = next((c for c in sorted(_CATEGORIES, key=len, reverse=True) if before.endswith(c)), None)
        if not category:   # label may sit inside the link instead
            category = next((c for c in sorted(_CATEGORIES, key=len, reverse=True) if txt.startswith(c)), None)
        name_m = re.search(r"(.+?\[[A-Za-z0-9\-]+\])", re.sub(rf"^(?:{'|'.join(_CATEGORIES)})\s*", "", txt))
        name = name_m.group(1).strip() if name_m else None
        date_m = re.search(r"(?:Release Date|発売日)\s*:?\s*(.+?)(?:\s+(?:MSRP|メーカー希望小売価格)|$)", txt)
        msrp_m = re.search(r"(?:MSRP|メーカー希望小売価格)\s*(\S+)", txt)
        img = re.search(r'<img[^>]+src="([^"]+)"', inner)
        code_m = re.search(r"\[([A-Za-z0-9\-]+)\]", txt)
        cards.append({
            "url": full_url(m.group(1), page_url), "slug": m.group(2).lower(),
            "category": _CAT_EN.get(category, category) if category else None,
            "name": name, "code": code_m.group(1).upper() if code_m else None,
            "date_text": date_m.group(1).strip() if date_m else "",
            "msrp": msrp_m.group(1) if msrp_m else None,
            "thumb": full_url(img.group(1), page_url) if img else None,
        })
    return cards


def all_list_cards(lang, min_passes=2, max_passes=6):
    """Every product card on every list page. The site orders products that share a
    release date differently on each request, so a product can move between pages;
    the whole list is read again until a full pass finds nothing new (2 to 6 passes)."""
    cards, seen, problems = [], set(), []
    total = None
    for done in range(max_passes):
        before = len(cards)
        n = 1
        while n <= (total or 1) and n < 40:
            url = f"{BASE}/{lang}/products/list.php" + ("" if n == 1 else f"?page={n}")
            try:
                page = _get(url)
            except requests.exceptions.RequestException as e:
                problems.append((url, f"could not fetch: {e}"))
                break
            if total is None:
                m = re.search(r"\b1\s*/\s*(\d+)\b", clean(strip_scripts(page))[-3000:])
                total = int(m.group(1)) if m else 1
            for c in list_cards(page, url):
                if c["slug"] not in seen:
                    seen.add(c["slug"])
                    cards.append(c)
            n += 1
            time.sleep(0.5)
        if done + 1 >= min_passes and len(cards) == before:
            break   # a full pass found nothing new
    return cards, problems


# ---------------------------------------------------------------------------
# Product pages
# ---------------------------------------------------------------------------
def x_items(text):
    """'x1 Ready-to-play 50-card deck x10 Resource Cards' -> ['1 x Ready-to-play 50-card deck', '10 x Resource Cards']"""
    parts = re.split(r"(?:^|\s)[x×](\d+)\s+", " " + text)
    out = []
    for i in range(1, len(parts) - 1, 2):
        item = parts[i + 1].strip(" ,;")
        if item:
            out.append(f"{parts[i]} x {item}")
    return out


def jp_items(text):
    """'デッキ: 50枚 リソースカード: 10枚' -> ['50 x デッキ', '10 x リソースカード']"""
    out = []
    for name, n, unit in re.findall(r"(\S+?)\s*[:：]\s*(\d+)\s*(枚|個|パック|体|冊|本|セット)", text):
        out.append(f"{n} x {name}" + ("" if unit == "枚" else f" ({n}{unit})"))
    return out


def detail_info(page, page_url, lang):
    body = clean(strip_scripts(page))
    info = {"contents": [], "image": None, "image_note": None, "date_text": ""}
    d = re.search(r"(?:Release Date|発売日)\s*:?\s*(.+?)(?:\s+(?:MSRP|メーカー希望小売価格|FEATURED|Release)|$)", body)
    if d:
        info["date_text"] = d.group(1).strip()
    # contents
    c = re.search(r"(?:Contents|内容|商品内容|セット内容|封入内容)\s*:?\s*(.+?)\s*" + _STOP, body)
    if c:
        raw = c.group(1).strip()
        pack = (re.match(r"1\s*Pack\s*:?\s*(\d+(?:\s*\+\s*\d+)?)\s*Cards?", raw, re.I)
                or re.match(r"1\s*パック\s*(?:カード)?\s*[:：]?\s*(\d+\s*枚?(?:\s*\+\s*\d+\s*枚?)?)", raw))
        if pack:
            n = re.sub(r"\s|枚", "", pack.group(1))
            info["contents"].append(f"Pack configuration: {n} cards per pack")
        else:
            items = x_items(raw) or jp_items(raw)
            info["contents"] += items if items else [raw[:300]]
    r = re.search(r"(?:Rarity|レアリティ)\s*:?\s*(.+?)\s*(?:" + _JUNK + r"|$)", body)
    if r:
        info["contents"].append("Rarity: " + r.group(1).strip()[:300])
    dt = re.search(r"(?:Design Types|デザイン)\s*:?\s*(.+?)\s*(?:Contents|内容|" + _JUNK + r"|$)", body)
    if dt:
        info["contents"].append("Design types: " + dt.group(1).strip()[:300])
    # main image: first product photo in the page body, skipping card samples and "coming soon"
    main = strip_scripts(page)
    for src in re.findall(r'<img[^>]+src="([^"]+)"', main):
        s = src.lower()
        if "/gcg/bccard/" not in s:
            continue
        if "dummy" in s or "sample" in s or re.search(r"(?<![a-z])[a-z]{2}\d{2}[-_]\d{3}(?!\d)", s):   # card images, e.g. ST13-001
            continue
        if "comingsoon" in s:
            info["image_note"] = "official page shows a 'coming soon' placeholder"
            continue
        info["image"] = full_url(src, page_url)
        info["image_note"] = None
        break
    return info


def get_all_products():
    products, skipped, problems = [], [], []
    today = date.today().isoformat()
    lists = {}
    for lang in _LANGS:
        cards, probs = all_list_cards(lang)
        problems += [(lang, u, w) for u, w in probs]
        lists[lang] = cards
    # Safety net: EN and JP share product codes. A product in one list but missing
    # from the other is looked up directly on its own page (404 = not sold there).
    if len(lists) > 1:
        for lang, cards in lists.items():
            have = {c["slug"] for c in cards}
            for other, other_cards in lists.items():
                for oc in other_cards:
                    if other == lang or oc["slug"] in have:
                        continue
                    have.add(oc["slug"])
                    cards.append({"url": f"{BASE}/{lang}/products/{oc['slug']}.html", "slug": oc["slug"],
                                  "category": oc["category"], "name": None, "code": oc["code"],
                                  "date_text": "", "msrp": None, "thumb": None, "cross_filled": True})
    for lang in _LANGS:
        for c in lists[lang]:
            try:
                page = _get(c["url"])
                info = detail_info(page, c["url"], lang)
            except requests.exceptions.RequestException as e:
                if c.get("cross_filled"):
                    continue   # not sold in this language
                problems.append((lang, c["url"], f"could not fetch: {e}"))
                page, info = "", {"contents": [], "image": None, "image_note": None, "date_text": ""}
            time.sleep(0.3)
            rel = to_iso(c["date_text"]) or to_iso(info["date_text"])
            if c.get("cross_filled") and not info["date_text"] and not info["contents"]:
                continue   # page exists but has no product details
            how = (f'official product list: "{c["date_text"]}"' if to_iso(c["date_text"])
                   else f'official product page: "{info["date_text"]}"' if rel
                   else f'no exact date on the site (list: "{c["date_text"]}")')
            t = re.search(r"<title>(.*?)\|", page or "", re.S)
            name = c["name"] or (clean(t.group(1)) if t else c["slug"].upper())
            if c.get("cross_filled"):
                how += " (found on its product page; it was missing from this language's product list)"
            image, note = info["image"], info["image_note"]
            if not image and c["thumb"] and "comingsoon" not in c["thumb"].lower():
                image, note = c["thumb"], "product-list thumbnail (no main photo on the product page)"
            p = {"lang": lang, "name": name, "code": c["code"], "category": c["category"] or "OTHER",
                 "release_date": rel, "release_how": how, "image": image, "image_note": note,
                 "contents": info["contents"], "msrp": c["msrp"], "source_url": c["url"], "_page": page}
            if rel and rel > today:
                skipped.append((lang, name, f"releases {rel}"))
                continue
            products.append(p)
    unique, keys = [], set()
    for p in products:
        k = (p["lang"], p["code"] or p["source_url"])
        if k in keys:
            continue
        keys.add(k)
        unique.append(p)
    unique.sort(key=lambda p: (p["lang"], p["release_date"] or "9999", p["name"]))
    return unique, skipped, problems


def build_record(p):
    return make_product_record(
        game=f"Gundam Card Game ({p['lang'].upper()})",
        title=p["name"],
        sku=p["code"],
        release_date=p["release_date"],
        image_url=p["image"],
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["source_url"],
    )


def print_notes(p):
    print(f"Category: {p['category']}" + (f" | MSRP: {p['msrp']}" if p["msrp"] and p["msrp"] != "-" else ""))
    print(f"Release note: {p['release_how']}" if p["release_date"] else f"Release note: NO DATE -- {p['release_how']}")
    if p["image_note"]:
        print(f"Image note: {p['image_note']}")


def discover_all():
    products, skipped, problems = get_all_products()
    for lang in _LANGS:
        mine = [p for p in products if p["lang"] == lang]
        cats = {}
        for p in mine:
            cats[p["category"]] = cats.get(p["category"], 0) + 1
        print(f"[DEBUG] Found {len(mine)} Gundam Card Game {lang.upper()} products: "
              + ", ".join(f"{k} {v}" for k, v in sorted(cats.items())))
    for p in products:
        print_product_record(build_record(p))
        print_notes(p)
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for lang, n, why in skipped:
        print(f"  - [{lang.upper()}] {n}: {why}")
    print(f"\n{len(problems)} problem(s):")
    for lang, u, why in problems:
        print(f"  - [{lang.upper()}] {u}: {why}")
    for label, test in (("no release date", lambda p: not p["release_date"]),
                        ("no image", lambda p: not p["image"]),
                        ("no contents", lambda p: not p["contents"])):
        bad = [p for p in products if test(p)]
        print(f"\n{len(bad)} product(s) with {label}:")
        for p in bad:
            print(f"  - [{p['lang'].upper()}] {p['name']}  {p['source_url']}")


def discover(term):
    products, skipped, _ = get_all_products()
    terms = [t.strip().lower() for t in term.split("|") if t.strip()]
    matches = [p for p in products if any(t in p["name"].lower() or t == (p["code"] or "").lower() for t in terms)]
    if not matches:
        print(f"No product found matching '{term}'")
        for lang, n, why in skipped:
            if any(t in n.lower() for t in terms):
                print(f"  (skipped as not released yet: [{lang.upper()}] {n}, {why})")
    for p in matches:
        print_product_record(build_record(p))
        print_notes(p)
        if _DEBUG:
            body = clean(strip_scripts(p["_page"]))
            i = max(body.find("Release Date"), body.find("発売日"))
            print(f"[DEBUG] page text near the details:\n  {body[max(0, i - 200): i + 1500]}")
            print("[DEBUG] images on the page:")
            for s in re.findall(r'<img[^>]+src="([^"]+)"', strip_scripts(p["_page"])):
                if "/gcg/" in s:
                    print(f"  {s}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() != "all":
        discover(sys.argv[1])
    elif len(sys.argv) >= 2:
        discover_all()
    else:
        term = input("Product name or code to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        discover(term) if term else discover_all()