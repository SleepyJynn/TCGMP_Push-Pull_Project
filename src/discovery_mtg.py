"""
Discovery script for Pokémon TCG sealed products, ENGLISH + JAPANESE.

JP  (official): pokemon-card.com's product search API /products/resultAPI.php
    (found in /assets/js/products/bundle.js, pokemon_diag4.py, 10 Nov 2026).
    Every product type on the site (拡張パック, 構築デッキ, その他の商品,
    周辺グッズ), 1996 to today, with official image, exact date and contents.
    Contents = description lines, shop notes (※ lines, 【取り扱い店舗】) dropped.

EN  (INTERIM, WITH GAPS): pokemon.com blocks automated requests (Imperva
    "Pardon Our Interruption" on every request, 10 Nov 2026) and its terms
    forbid bulk downloading; TCGplayer / Star City Games terms ban scraping.
    Open, licensed sources are used until the supervisor confirms an image
    source (distributor feed / permission):
      pkmjson  https://github.com/ArthurG-data/pkmjson   data: CC BY 4.0
               (credit required; most EN contents came from TCGplayer
               product text via tcgcsv.com -- flagged to the supervisor)
      TCGdex   https://api.tcgdex.net   MIT -- set release dates, promo card names
    EN date = product's own pkmjson date, else the set's TCGdex date (noted),
    none for products filed under promo sets (svp, swshp, ...).
    EN image = blank on purpose. EN leaves out cases and "Set of N" groupings.

Products releasing after today are skipped (same rule as the other games).

Usage:
    python src/discovery_pokemon.py all --out=pokemon_output.txt
    python src/discovery_pokemon.py all --lang=jp --out=pokemon_jp.txt
    python src/discovery_pokemon.py all --lang=en --out=pokemon_en.txt
    python src/discovery_pokemon.py "Surging Sparks|ハドウシーカー" --out=pokemon_search.txt
    --refresh  re-download pkmjson/TCGdex data instead of using pkm_cache/
    --debug    extra detail on searched products
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


def _flag(name):
    if name in sys.argv:
        sys.argv.remove(name)
        return True
    return False


_REFRESH = _flag("--refresh")
_DEBUG = _flag("--debug")
_LANGS = ["en", "jp"]
if any(a.startswith("--lang=") for a in sys.argv):
    _lang_arg = next(a for a in sys.argv if a.startswith("--lang="))
    sys.argv.remove(_lang_arg)
    _LANGS = [_lang_arg.split("=", 1)[1].lower()]

import html as html_lib
import io
import json
import os
import re
import time
import zipfile
from collections import Counter
from datetime import date

import requests
from common import make_product_record, print_product_record


# ================================================================ ENGLISH
PKMJSON_ZIP = "https://codeload.github.com/ArthurG-data/pkmjson/zip/refs/heads/main"
PKMJSON_WEB = "https://github.com/ArthurG-data/pkmjson/blob/main/"
TCGDEX = "https://api.tcgdex.net/v2/en"
CACHE = "pkm_cache"
CREDIT = "pkmjson by ArthurG-data (CC BY 4.0); set dates and card names from TCGdex (MIT)"
IMAGE_NOTE = ("left blank on purpose: no allowed source has English product images "
              "(pokemon.com blocks scraping); pending supervisor (distributor feed / permission)")
_PROMO_SETS = re.compile(r"^(basep|np|dpp|hgssp|bwp|xyp|smp|swshp|svp|mep|wp)$|p$")

en_session = requests.Session()
en_session.headers.update({"User-Agent": "tcg-migration discovery (intern project)"})


# ---------------------------------------------------------------- downloads
def _get(url, binary=False):
    last = None
    for attempt in range(3):
        try:
            r = en_session.get(url, timeout=120)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content if binary else r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def load_pkmjson():
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "pkmjson.zip")
    if _REFRESH or not os.path.exists(path):
        raw = _get(PKMJSON_ZIP, binary=True)
        if not raw:
            raise RuntimeError("could not download pkmjson")
        with open(path, "wb") as f:
            f.write(raw)
    families = {}
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            m = re.search(r"/(data/international/[^/]+/[^/]+\.json)$", n)
            if m:
                d = json.loads(z.read(n).decode("utf-8"))
                d["_path"] = m.group(1)
                families[d["id"]] = d
    return families


def _json_cache(name):
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and not _REFRESH:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(name, data):
    with open(os.path.join(CACHE, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def set_dates(set_ids):
    cache = _json_cache("tcgdex_sets.json")
    for sid in sorted(set_ids):
        if sid in cache:
            continue
        try:
            d = _get(f"{TCGDEX}/sets/{sid}")
            cache[sid] = {"name": (d or {}).get("name"), "releaseDate": (d or {}).get("releaseDate")}
        except RuntimeError as e:
            cache[sid] = {"name": None, "releaseDate": None, "error": str(e)}
        time.sleep(0.1)
    _save_cache("tcgdex_sets.json", cache)
    return cache


def card_names(card_ids):
    cache = _json_cache("tcgdex_cards.json")
    for cid in sorted(card_ids):
        if cid in cache:
            continue
        try:
            d = _get(f"{TCGDEX}/cards/{cid}")
            cache[cid] = (d or {}).get("name")
        except RuntimeError:
            cache[cid] = None
        time.sleep(0.1)
    _save_cache("tcgdex_cards.json", cache)
    return cache


# ---------------------------------------------------------------- formatting
def excluded_reason(fam, name):
    if fam.get("category") == "case" or re.search(r"\bCase\s*$", name, re.I):
        return "case"
    if re.search(r"\bSet of \d+\b", name, re.I):
        return "Set of N grouping"
    return None


def render_contents(contents, families, cards, set_info):
    lines = []
    for s in contents.get("sealed", []):
        inner = families.get(s.get("product"), {})
        en = next((r["name"] for r in inner.get("releases", []) if r.get("language") == "en"), None)
        label = en or inner.get("name") or s.get("product")
        if inner.get("category") in ("booster_pack", "sleeved_booster_pack") and "pack" not in label.lower():
            label += " booster pack"
        lines.append(f"{s.get('count', 1)} x {label}")
    for c in contents.get("cards", []):
        cid = c.get("tcgdexCardId", "?")
        nm = cards.get(cid)
        role = c.get("role")
        lines.append(f"{c.get('count', 1)} x {nm + ' ' if nm else ''}card ({cid})"
                     + (f" [{role}]" if role else ""))
    for o in contents.get("other", []):
        lines.append(f"{o.get('count', 1)} x {o.get('name', '?')}")
    pool = contents.get("pool") or []
    if pool:
        names = [(set_info.get(p["set"]) or {}).get("name") or p["set"] for p in pool]
        lines.append("Cards from: " + ", ".join(names))
    return lines


# ---------------------------------------------------------------- discovery
def en_get_all_products():
    families = load_pkmjson()
    en = []
    for fam in families.values():
        for r in fam.get("releases", []):
            if r.get("language") == "en":
                en.append((fam, r))
    set_info = set_dates({fam["set"] for fam, _ in en}
                         | {p["set"] for fam, _ in en for p in (fam.get("contents") or {}).get("pool", [])})
    cards = card_names({c["tcgdexCardId"] for fam, r in en
                        for c in ((r.get("contents") or fam.get("contents") or {}).get("cards", []))
                        if c.get("tcgdexCardId")})
    today = date.today().isoformat()
    products, skipped, excluded = [], [], Counter()
    for fam, r in en:
        name = r.get("name") or fam.get("name")
        why = excluded_reason(fam, name)
        if why:
            excluded[why] += 1
            continue
        own = r.get("releaseDate")
        sid = fam["set"]
        info = set_info.get(sid) or {}
        if own:
            rel, how = own, "product release date (pkmjson)"
        elif _PROMO_SETS.search(sid):
            rel, how = None, f"no product date in pkmjson; filed under promo set '{sid}', so no set-date fallback"
        elif info.get("releaseDate"):
            rel, how = info["releaseDate"][:10], f"set release date of {info.get('name') or sid} (TCGdex); pkmjson has no product date"
        else:
            rel, how = None, "no date in pkmjson or TCGdex"
        if rel and rel > today:
            skipped.append((name, f"releases {rel}"))
            continue
        contents = r.get("contents") or fam.get("contents") or {}
        products.append({
            "name": name,
            "set": info.get("name") or sid,
            "set_code": sid,
            "category": fam.get("category"),
            "release_date": rel,
            "release_how": how,
            "contents": render_contents(contents, families, cards, set_info),
            "status": fam.get("contentsStatus"),
            "sources": r.get("sources") or [],
            "source_url": PKMJSON_WEB + fam["_path"],
        })
    unique, keys = [], set()
    for p in products:
        k = (re.sub(r"[^a-z0-9]", "", p["name"].lower()), p["release_date"])
        if k in keys:
            continue
        keys.add(k)
        unique.append(p)
    unique.sort(key=lambda p: (p["release_date"] or "9999", p["name"]))
    en_get_all_products.excluded = excluded
    return unique, skipped


def en_build_record(p):
    return make_product_record(
        game="Pokémon TCG (EN)",
        title=p["name"],
        sku=None,
        release_date=p["release_date"],
        image_url=None,
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["source_url"],
    )


def en_print_notes(p):
    print(f"Set: {p['set']} ({p['set_code']}) | Category: {p['category']}")
    print(f"Release note: {p['release_how']}" if p["release_date"]
          else f"Release note: NO DATE -- {p['release_how']}")
    print(f"Image note: {IMAGE_NOTE}")
    if p["status"] == "partial":
        print("Contents note: pkmjson marks these contents as partial")
    print(f"Credit: {CREDIT}")




# ================================================================ JAPANESE
BASE = "https://www.pokemon-card.com"
API = BASE + "/products/resultAPI.php"
jp_session = requests.Session()
jp_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
    "Referer": BASE + "/products/index.html",
})


# ---------------------------------------------------------------- fetching
def fetch_page(page):
    last = None
    for attempt in range(3):
        try:
            r = jp_session.get(API, params={"page": page}, timeout=30)
            r.raise_for_status()
            data = r.json()
            if str(data.get("result")) != "1":
                raise ValueError(f"API error: {data.get('errMsg')}")
            return data
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"page {page}: {last}")


def fetch_all():
    first = fetch_page(1)
    max_page = int(first.get("maxPage") or 1)
    hit_cnt = int(first.get("hitCnt") or 0)
    items, problems = list(first.get("products") or []), []
    for page in range(2, max_page + 1):
        time.sleep(0.5)
        try:
            items += fetch_page(page).get("products") or []
        except RuntimeError as e:
            problems.append(str(e))
    return items, hit_cnt, max_page, problems


# ---------------------------------------------------------------- parsing
_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_MONTH = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")


def parse_date(text):
    text = text or ""
    m = _DATE.search(text)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d).isoformat(), "official release date (pokemon-card.com)"
        except ValueError:
            pass
    m = _MONTH.search(text)
    if m:
        return None, f"month only on the site: '{text.strip()}'"
    return None, f"no date on the site: '{text.strip()}'"


def clean_lines(desc):
    s = desc or ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s).replace("　", " ")
    return [re.sub(r"\s+", " ", x).strip() for x in s.split("\n")]


def contents_from(desc):
    out = []
    for line in clean_lines(desc):
        if not line:
            continue
        if line.startswith("【"):        # 【取り扱い店舗に関して】 etc: shop notes follow
            break
        if line.startswith(("※", "＊", "*")):
            continue
        line = line.lstrip("・･ ").strip()
        if line:
            out.append(line)
    return out


def absolute(url):
    url = (url or "").strip()
    if not url:
        return None
    return url if url.startswith("http") else BASE + ("" if url.startswith("/") else "/") + url


def norm(s):
    return re.sub(r"[\s「」『』・･\-–—]", "", (s or "")).lower()


def jp_get_all_products():
    raw, hit_cnt, max_page, problems = fetch_all()
    today = date.today().isoformat()
    products, skipped, seen = [], [], set()
    for it in raw:
        name = html_lib.unescape((it.get("productTitle") or "").strip())
        rel, how = parse_date(it.get("releaseDate"))
        img = absolute(it.get("tumbsImg"))
        key = (norm(name), rel or it.get("releaseDate"), img)
        if key in seen:
            continue
        seen.add(key)
        if rel and rel > today:
            skipped.append((name, f"releases {rel}"))
            continue
        detail = absolute(it.get("link_detailPage"))
        products.append({
            "name": name,
            "type": (it.get("productType") or "").strip(),
            "release_date": rel,
            "release_how": how,
            "image": img,
            "contents": contents_from(it.get("description")),
            "stores": (it.get("storesAvailable") or "").strip(),
            "price_txt": (it.get("priceTxt") or "").strip(),
            "source_url": detail or BASE + "/products/index.html",
            "pc_url": absolute(it.get("link_pokemonCenter")),
            "raw_desc": it.get("description") or "",
        })
    # same product listed twice with a different image file: keep the first
    unique, keys = [], set()
    for p in products:
        k = (norm(p["name"]), p["release_date"])
        if k in keys:
            continue
        keys.add(k)
        unique.append(p)
    unique.sort(key=lambda p: (p["release_date"] or "9999", p["name"]))
    jp_get_all_products.stats = {"hit_cnt": hit_cnt, "max_page": max_page, "fetched": len(raw)}
    return unique, skipped, problems


def jp_build_record(p):
    return make_product_record(
        game="Pokémon TCG (JP)",
        title=p["name"],
        sku=None,
        release_date=p["release_date"],
        image_url=p["image"],
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["source_url"],
    )


def jp_print_notes(p):
    print(f"Type: {p['type']}" + (f" | Sold at: {p['stores']}" if p["stores"] else ""))
    print(f"Release note: {p['release_how']}" if p["release_date"]
          else f"Release note: NO DATE -- {p['release_how']}")
    if not p["image"]:
        print("Image note: no image in the site's product search")




# ================================================================ combined
def get_all_products():
    products, skipped, problems, notes = [], [], [], []
    if "en" in _LANGS:
        try:
            ps, sk = en_get_all_products()
            for p in ps:
                p["lang"] = "en"
            products += ps
            skipped += [("en", n, w) for n, w in sk]
            exc = getattr(en_get_all_products, "excluded", {})
            hows = Counter("own date" if p["release_how"].startswith("product") else
                           "set date" if p["release_how"].startswith("set") else "no date" for p in ps)
            notes.append(f"EN left out per scope: " + (", ".join(f"{k} {v}" for k, v in exc.items()) or "none"))
            notes.append("EN dates: " + ", ".join(f"{k} {v}" for k, v in hows.items()))
        except RuntimeError as e:
            problems.append(("en", "pkmjson/TCGdex", str(e)))
    if "jp" in _LANGS:
        try:
            ps, sk, pr = jp_get_all_products()
            for p in ps:
                p["lang"] = "jp"
            products += ps
            skipped += [("jp", n, w) for n, w in sk]
            problems += [("jp", "resultAPI.php", w) for w in pr]
            st = getattr(jp_get_all_products, "stats", {})
            notes.append(f"JP site reports {st.get('hit_cnt')} products on {st.get('max_page')} pages; fetched {st.get('fetched')}")
        except RuntimeError as e:
            problems.append(("jp", "resultAPI.php", str(e)))
    return products, skipped, problems, notes


def build_record(p):
    return en_build_record(p) if p["lang"] == "en" else jp_build_record(p)


def print_notes(p):
    en_print_notes(p) if p["lang"] == "en" else jp_print_notes(p)


def discover_all():
    products, skipped, problems, notes = get_all_products()
    for lang in _LANGS:
        mine = [p for p in products if p["lang"] == lang]
        years = sorted(p["release_date"][:4] for p in mine if p["release_date"])
        key = "category" if lang == "en" else "type"
        print(f"[DEBUG] Found {len(mine)} Pokémon {lang.upper()} products"
              + (f", {years[0]} to {years[-1]}" if years else ""))
        print(f"[DEBUG]   by {key}: " + ", ".join(f"{c} {n}" for c, n in Counter(p[key] for p in mine).most_common()))
    for n in notes:
        print(f"[DEBUG] {n}")
    if "en" in _LANGS:
        print(f"[DEBUG] EN credit: {CREDIT}")
    for p in products:
        print_product_record(build_record(p))
        print_notes(p)
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for lang, n, why in skipped:
        print(f"  - [{lang.upper()}] {n}: {why}")
    print(f"\n{len(problems)} problem(s):")
    for lang, n, why in problems:
        print(f"  - [{lang.upper()}] {n}: {why}")
    for label, test in (("no release date", lambda p: not p["release_date"]),
                        ("no image (JP only; EN images are blank on purpose)", lambda p: p["lang"] == "jp" and not p["image"]),
                        ("no contents", lambda p: not p["contents"])):
        bad = [p for p in products if test(p)]
        print(f"\n{len(bad)} product(s) with {label}:")
        for p in bad[:300]:
            print(f"  - [{p['lang'].upper()}] {p['name']}  {p['release_date'] or p['release_how']}")
        if len(bad) > 300:
            print(f"  ... and {len(bad) - 300} more")
    if "en" in _LANGS:
        n_en = sum(1 for p in products if p["lang"] == "en")
        n_part = sum(1 for p in products if p["lang"] == "en" and p.get("status") == "partial")
        print(f"\n[EN] {n_part} product(s) marked partial by pkmjson (mostly booster packs: random cards)")
        print(f"[EN] All {n_en} EN products have no image: {IMAGE_NOTE}")


def discover(term):
    products, skipped, _, _ = get_all_products()
    terms = [t.strip().lower() for t in term.split("|") if t.strip()]
    matches = [p for p in products if any(
        t in p["name"].lower() or (p["lang"] == "en" and (t in (p["set"] or "").lower() or t == p["set_code"].lower()))
        for t in terms)]
    if not matches:
        print(f"No product found matching '{term}'")
        for lang, n, why in skipped:
            if any(t in n.lower() for t in terms):
                print(f"  (skipped as not released yet: [{lang.upper()}] {n}, {why})")
    for p in matches:
        print_product_record(build_record(p))
        print_notes(p)
        if _DEBUG:
            if p["lang"] == "en":
                print("[DEBUG] pkmjson sources: " + " | ".join(p["sources"][:5]))
            else:
                print(f"[DEBUG] raw description: {p['raw_desc'][:1500]}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() != "all":
        discover(sys.argv[1])
    elif len(sys.argv) >= 2:
        discover_all()
    else:
        term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        discover(term) if term else discover_all()