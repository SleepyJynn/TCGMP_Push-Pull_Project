"""
Discovery script for hololive OFFICIAL CARD GAME (Bushiroad / COVER) sealed
products, EN and JP.

Sources (both official, both WordPress):
  EN  https://en.hololive-official-cardgame.com
  JP  https://hololive-official-cardgame.com
  - The product list comes from each site's own data feed:
    /wp-json/wp/v2/post_product?per_page=100&_embed=1
    (title, link, category BOOSTERS / DECKS / Accessories, featured image).
    Confirmed by the 2 Nov 2026 diagnostic runs: EN 36 entries, JP 95.
  - Release date and contents are NOT in the feed (its content field is
    empty); they are read from each product's own page ("Release Date March
    19 (Fri), 2027" / "発売日 2026年9月19日（土）").

Cleaning (decided 2 Nov 2026):
  - The feed has duplicate entries (EN lists Ayakashi Vermilion, Enchant
    Regalia, Advent/Justice and Live Start Deck (6 Types) twice): duplicates
    by page link, then by same name + same release date, are dropped.
  - "Umbrella" pages that just group decks sold separately ("Start Deck –
    Oshi Advent / Oshi Justice", "Live Start Deck (6 Types)", JP 「白 轟はじめ／
    緑 風真いろは／黄 不知火フレア」) are skipped -- each deck has its own page.
  - JP "_list" pages are deck-list pages for a deck already listed: skipped.
  - Event / hololive production OFFICIAL SHOP exclusive items (【イベント物販／
    ...OFFICIAL SHOP限定商品】, the Christmas / Anniversary sets) are left out,
    the same scope as the Hololive tracker. Set INCLUDE_SHOP_EXCLUSIVES = True
    to include them.
  - Not released yet (date after today, or "Coming soon") -> skipped.

Usage:
    python src/discovery_hololive.py all --out=hololive_output.txt
    python src/discovery_hololive.py "Volume Vortex" --debug --out=hololive_debug.txt
    python src/discovery_hololive.py all --lang=en   (or --lang=jp)
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

import requests
from common import make_product_record, print_product_record

SITES = {"en": "https://en.hololive-official-cardgame.com", "jp": "https://hololive-official-cardgame.com"}
INCLUDE_SHOP_EXCLUSIVES = False

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
})

_SHOP_EXCLUSIVE_RE = re.compile(r"OFFICIAL SHOP限定|イベント物販|Anniversary|クリスマススペシャルセット|スタートデッキセット|Debut Set", re.I)
_UMBRELLA_RE = re.compile(r"\s/\s|／|\(6 Types\)|（全6種）|\(全6種\)|全6種|推し [^ ]+/[^ ]+$")
_TYPE_BY_CAT = {"boosters": "Booster", "decks": "Start Deck", "accessories": "Accessory"}


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


def page_text(page):
    return clean(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S))


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------
def fetch_feed(lang):
    base = SITES[lang]
    items, page = [], 1
    while True:
        r = _get(f"{base}/wp-json/wp/v2/post_product?per_page=100&page={page}&_embed=1")
        items += r.json()
        if page >= int(r.headers.get("X-WP-TotalPages") or 1):
            break
        page += 1
    out = []
    for it in items:
        emb = it.get("_embedded") or {}
        media = (emb.get("wp:featuredmedia") or [{}])[0] or {}
        terms = [t for group in (emb.get("wp:term") or []) for t in group if t.get("taxonomy") == "cat_product"]
        cat = terms[0]["slug"] if terms else ""
        out.append({
            "lang": lang, "id": it.get("id"), "slug": it.get("slug"),
            "title": clean((it.get("title") or {}).get("rendered")),
            "link": it.get("link") or "", "category": cat,
            "type": _TYPE_BY_CAT.get(cat, cat or "Other"),
            "image": media.get("source_url"),
        })
    return out


# ---------------------------------------------------------------------------
# Product page
# ---------------------------------------------------------------------------
_EN_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def release_from(text, lang):
    """(iso date or None, the raw date text)"""
    if lang == "en":
        # FIXED 2 Nov 2026 (first live run): older pages write "April 17th, 2026"
        # and some put the date under a different label, so any
        # "Month D(th), YYYY" in the product section is accepted, preferring
        # the one right after "Release Date".
        date_re = rf"((?:{_EN_MONTHS})\s+\d{{1,2}})(?:st|nd|rd|th)?(?:\s*\([A-Za-z.]+\))?,?\s*(\d{{4}})"
        m = re.search(r"Release Date\s+" + date_re, text, re.I) or re.search(date_re, text)
        if m:
            raw = f"{m.group(1)}, {m.group(2)}"
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%B %d %Y").date().isoformat(), raw
        m = re.search(r"(\d{4})[./](\d{1,2})[./](\d{1,2})", text)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), m.group(0)
        m = re.search(r"Release Date\s+(\S[^A-Z]{0,40})", text)
        return None, (m.group(1).strip() if m else "")
    m = re.search(r"発売日\s*(.{0,60}?)(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if m:
        return date(int(m.group(2)), int(m.group(3)), int(m.group(4))).isoformat(), m.group(0)
    m = re.search(r"発売日\s*(\S{0,30})", text)
    return None, (m.group(1) if m else "")


def section(text, lang):
    """The product's own text (between the overview heading and 'back to list')."""
    if lang == "en":
        start = text.find("Overview")
        end = text.find("Back to List", start)
    else:
        start = text.find("概要")
        end = text.find("一覧にもどる", start)
    if start == -1:
        return ""
    return text[start:end if end != -1 else start + 6000]


def contents_from(sec, lang, ptype, name=""):
    lines = []
    # Booster pack / box configuration (JP puts it in the price line)
    pack = re.search(r"1パック[（(](\d+)枚入り[）)]", sec) or re.search(r"(\d+)\s*cards?\s*(?:per|/|in each)\s*pack", sec, re.I)
    box = re.search(r"1ボックス[（(](\d+)パック入り[）)]", sec) or re.search(r"(\d+)\s*packs?\s*(?:per|/|in each)\s*box", sec, re.I)
    if pack or box:
        bits = ([f"{pack.group(1)} cards per pack"] if pack else []) + ([f"{box.group(1)} packs per box"] if box else [])
        lines.append("Pack configuration: " + "; ".join(bits))
    # Rarity: "Standard – 125 types OSR: 7 RR: 12 ..." (EN) / similar with full-width colons (JP)
    for m in re.finditer(r"(Standard|Parallel|通常|パラレル)[^0-9A-Z]{0,6}(\d+)\s*(?:types|種)?((?:\s*[A-Z]{1,4}\s*[:：]\s*\d+)+)", sec):
        pairs = "; ".join(f"{a} {b}" for a, b in re.findall(r"([A-Z]{1,4})\s*[:：]\s*(\d+)", m.group(3)))
        lines.append(f"Rarity ({m.group(1)}, {m.group(2)} types): {pairs}")
    # Deck parts: "Main Deck: 50 cards (Pre-constructed deck)" / "メインデッキ：50枚"
    items = re.findall(r"([A-Za-z][A-Za-z ’'\-]{2,40}?):\s*(\d+)\s*cards?(?:\s*\(([^)]{1,40})\))?", sec)
    items += [(a, b, "") for a, b in re.findall(r"([^\s：:]{2,20}(?:デッキ|カード|ホロメン|エール|マーカー))\s*[：:]\s*(\d+)枚", sec)]
    # FIXED 2 Nov 2026 (v2 run): EN single-deck pages list all decks of the
    # wave ("White Todoroki Hajime Main Deck ... Green Kazama Iroha Main Deck
    # ..."). Split into one group per "... Main Deck" and keep the group whose
    # label names this product.
    if name and sum(1 for a, _, _ in items if a.strip().endswith("Main Deck")) > 1:
        groups, cur = [], []
        for it in items:
            if it[0].strip().endswith("Main Deck") and cur:
                groups.append(cur)
                cur = []
            cur.append(it)
        groups.append(cur)
        words = [w for w in re.findall(r"[A-Za-z]{3,}", name) if w.lower() not in ("start", "deck", "live", "booster", "pack")]
        own = [g for g in groups if any(w.lower() in g[0][0].lower() for w in words)]
        if len(own) == 1:
            items = own[0]
    if items:
        fmt = lambda a, b, c: (f"{re.sub(r'^(?:Contents|Overview)\s+', '', a.strip())}: {b} card{'' if b == '1' else 's'}"
                               + (f" ({c})" if c else ""))
        lines.append("Includes: " + "; ".join(fmt(a, b, c) for a, b, c in items))
    # Accessory specs: "内容 55枚/全3種 サイズ 約H92×W66mm"
    specs = re.findall(r"内容\s+(\S+(?:\s\S+)?)\s+サイズ\s+(\S+)", sec)
    if specs:
        lines.append("Includes: " + "; ".join(f"{a} (size {s})" for a, s in specs))
    if not lines:
        body = re.split(r"Contents|セット内容|商品内容|商品紹介", sec, maxsplit=1)
        txt = body[1] if len(body) > 1 else sec
        txt = re.sub(r"^(?:Overview|概要)\s*", "", txt).strip()
        txt = re.split(r"\s※|\s\*Photos|\s\*This product|\sFeatured Talents|\s推しホロメン", txt)[0]
        txt = txt.lstrip(":： ").strip()
        if txt:
            lines.append("Description: " + txt[:300])
    return lines


def page_image(page):
    """FIXED 2 Nov 2026: 23 JP products have no featured image in the feed.
    Fall back to the first product photo on the page itself (any attribute or
    CSS url(), since the theme lazy-loads), skipping banners/logos/icons."""
    for u in re.findall(r"(https?://[^\s\"'()<>]+/wp-content/uploads/[^\s\"'()<>]+\.(?:png|jpe?g|webp))", page):
        name = u.rsplit("/", 1)[-1].lower()
        if not re.search(r"^bnr|banner|logo|icon|ogp|thumb_column|kv\b", name):
            return u
    return None


def tidy_name(title, lang):
    return re.sub(r"^hololive OFFICIAL CARD GAME\s*", "", title).strip() if lang == "en" else title


def get_all_products():
    products, skipped, excluded, problems = [], [], [], []
    today = date.today()
    for lang in _LANGS:
        feed = fetch_feed(lang)
        seen_links, seen_name_date = set(), set()
        for it in feed:
            link = it["link"]
            if link in seen_links:
                excluded.append((lang, it["title"], "duplicate feed entry (same page)"))
                continue
            seen_links.add(link)
            if re.search(r"/products/post/?$", link):
                problems.append((lang, it["title"], f"feed entry has no product page link ({link})"))
                continue
            if link.rstrip("/").endswith("_list"):
                excluded.append((lang, it["title"], "deck-list page for a deck listed separately"))
                continue
            if _SHOP_EXCLUSIVE_RE.search(it["title"]):
                if not INCLUDE_SHOP_EXCLUSIVES:
                    excluded.append((lang, it["title"], "event / OFFICIAL SHOP exclusive"))
                    continue
            elif _UMBRELLA_RE.search(it["title"]):
                excluded.append((lang, it["title"], "group page for decks that each have their own page"))
                continue
            try:
                page = _get(link).text
            except requests.exceptions.RequestException as e:
                problems.append((lang, it["title"], f"could not fetch {link}: {e}"))
                continue
            text = page_text(page)
            sec = section(text, lang)
            if _SHOP_EXCLUSIVE_RE.search(sec[:200]) and not INCLUDE_SHOP_EXCLUSIVES:
                excluded.append((lang, it["title"], "event / OFFICIAL SHOP exclusive (per its page)"))
                continue
            if not it["image"]:
                it["image"] = page_image(page)
            iso, raw = release_from(sec or text, lang)
            name = tidy_name(it["title"], lang)
            if it["type"] == "Accessory" and lang == "jp":
                # JP accessories share generic names ("オフィシャルホロカスリーブ" x13):
                # the page slug tells them apart.
                tail = link.rstrip("/").rsplit("/", 1)[-1]
                name = f"{name} [{tail}]"
            if iso is None:
                skipped.append((lang, name, f"no release date yet (page says '{raw}')"))
                if _DEBUG:
                    print(f"[DEBUG] no date found on {link}:\n  {(sec or text)[:1500]}")
                continue
            if date.fromisoformat(iso) > today:
                skipped.append((lang, name, f"releases {iso}"))
                continue
            key = (re.sub(r"\W", "", name.lower()), iso)
            if key in seen_name_date:
                excluded.append((lang, it["title"], "duplicate feed entry (same name and date)"))
                continue
            seen_name_date.add(key)
            products.append({**it, "name": name, "release_date": iso, "release_raw": raw,
                             "contents": contents_from(sec, lang, it["type"], name), "_sec": sec})
    products.sort(key=lambda p: (p["lang"], p["release_date"], p["name"]))
    return products, skipped, excluded, problems


def build_record(p):
    return make_product_record(
        game=f"hololive OFFICIAL CARD GAME ({p['lang'].upper()})",
        title=p["name"],
        sku=None,
        release_date=p["release_date"],
        image_url=p["image"],
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["link"],
    )


def print_notes(p):
    print(f'Release note: on the product page: "{p["release_raw"]}"')
    if not p["image"]:
        print("Image note: no featured image in the site's product feed")


def discover_all():
    products, skipped, excluded, problems = get_all_products()
    for lang in _LANGS:
        mine = [p for p in products if p["lang"] == lang]
        print(f"[DEBUG] Found {len(mine)} hololive OCG {lang.upper()} products:")
        for p in mine:
            print(f"  - [{p['type']}] {p['name']}  ({p['release_date']})")
    for p in products:
        print_product_record(build_record(p))
        print_notes(p)
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for lang, n, why in skipped:
        print(f"  - [{lang.upper()}] {n}: {why}")
    print(f"\n{len(excluded)} feed entr(ies) left out on purpose:")
    for lang, n, why in excluded:
        print(f"  - [{lang.upper()}] {n}: {why}")
    print(f"\n{len(problems)} problem(s):")
    for lang, n, why in problems:
        print(f"  - [{lang.upper()}] {n}: {why}")
    for label, test in (("no image", lambda p: not p["image"]), ("no contents", lambda p: not p["contents"])):
        bad = [p for p in products if test(p)]
        print(f"\n{len(bad)} product(s) with {label}:")
        for p in bad:
            print(f"  - [{p['lang'].upper()}] {p['name']}  {p['link']}")


def discover(term):
    products, skipped, excluded, problems = get_all_products()
    terms = [t.strip().lower() for t in term.split("|") if t.strip()]
    matches = [p for p in products if any(t in p["name"].lower() for t in terms)]
    if not matches:
        print(f"No product found matching '{term}'")
        for lang, n, why in skipped + excluded:
            if any(t in n.lower() for t in terms):
                print(f"  ([{lang.upper()}] {n}: {why})")
    for p in matches:
        print_product_record(build_record(p))
        print_notes(p)
        if _DEBUG:
            print(f"[DEBUG] {p['lang'].upper()} page text used:\n  {p['_sec'][:2500]}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() != "all":
        discover(sys.argv[1])
    elif len(sys.argv) >= 2:
        discover_all()
    else:
        term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        discover(term) if term else discover_all()