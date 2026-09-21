"""
Discovery script for Riftbound sealed product data (Riot merch store).

Confirmed working sources (15 Sep 2026):
    Catalog: https://merch.riotgames.com/productPreviews/en-us.json.gz
    Product page: https://merch.riotgames.com/en-us/product/<slug>/

Usage:
    python src/discovery_riftbound.py "Vendetta"
"""

import gzip
import json
import re
import sys
import requests
from bs4 import BeautifulSoup
from common import make_product_record, print_product_record

CATALOG_URL = "https://merch.riotgames.com/productPreviews/en-us.json.gz"
WIKI_URL = "https://en.wikipedia.org/wiki/Riftbound"


def fetch_catalog():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    })
    resp = session.get(CATALOG_URL, timeout=20)
    resp.raise_for_status()
    return json.loads(gzip.decompress(resp.content))


def fetch_product_page_html(slug):
    url = f"https://merch.riotgames.com/en-us/product/{slug}/"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    })
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def extract_riftbound_contents(page_text):
    result = {"packs_per_display": None, "pack_breakdown": []}

    display_match = re.search(
        r"booster display contains (\d+) booster packs", page_text, re.IGNORECASE
    )
    if display_match:
        result["packs_per_display"] = int(display_match.group(1))

    breakdown_matches = re.findall(
        r"(\d+)\s+((?:foil\s+)?[A-Za-z][A-Za-z\s]*?)(?=,|\.|and\s+\d|$)",
        page_text,
    )
    for count, label in breakdown_matches:
        label_clean = label.strip().rstrip(",")
        if not label_clean or len(label_clean) >= 40:
            continue
        if "booster pack" in label_clean.lower() or "booster display" in label_clean.lower():
            continue
        result["pack_breakdown"].append(f"{count} {label_clean}")

    return result


def fetch_wikipedia_html():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    })
    resp = session.get(WIKI_URL, timeout=15)
    resp.raise_for_status()
    return resp.text


def find_release_date_near_term(html, search_term):
    for m in re.finditer(re.escape(search_term.lower()), html.lower()):
        idx = m.start()
        window = html[idx: idx + 600]
        date_match = re.search(r"[A-Za-z]+\s+\d{1,2},\s*\d{4}", window)
        if date_match:
            return date_match.group(0)
    return None


def discover(search_term):
    data = fetch_catalog()

    riftbound_items = {
        k: v for k, v in data.items()
        if (v.get("brand") or {}).get("slug") == "riftbound"
    }
    booster_items = {
        k: v for k, v in riftbound_items.items()
        if ("booster" in v.get("title", "").lower() or "display" in v.get("title", "").lower())
        and search_term.lower() in v.get("title", "").lower()
    }

    if not booster_items:
        print(f"No booster/display items found matching '{search_term}'")
        return

    try:
        wiki_html = fetch_wikipedia_html()
    except requests.exceptions.RequestException as e:
        print(f"(Couldn't fetch Wikipedia page: {e})")
        wiki_html = None

    records = []
    for sku, item in booster_items.items():
        slug = item.get("slug")
        contents_lines = []
        pack_config_lines = []

        try:
            page_html = fetch_product_page_html(slug)
            soup = BeautifulSoup(page_html, "html.parser")
            page_text = soup.get_text(separator="\n", strip=True)

            contents_line = None
            for line in page_text.split("\n"):
                if "contains" in line.lower() and "booster pack" in line.lower():
                    contents_line = line
                    break

            if contents_line:
                parsed = extract_riftbound_contents(contents_line)
                if parsed["packs_per_display"]:
                    contents_lines.append(f"{parsed['packs_per_display']} booster packs per display")
                pack_config_lines = parsed["pack_breakdown"]
        except requests.exceptions.RequestException as e:
            print(f"  (couldn't fetch page for {slug}: {e})")

        release_date = None
        if wiki_html:
            release_date = find_release_date_near_term(wiki_html, search_term)

        record = make_product_record(
            game="riftbound",
            title=item.get("title"),
            sku=sku,
            release_date=release_date,
            image_url=(item.get("main_image") or {}).get("src"),
            contents=contents_lines,
            pack_configuration=pack_config_lines,
            price=item.get("price"),
            source_url=f"https://merch.riotgames.com/en-us/product/{slug}/",
        )
        print_product_record(record)
        records.append(record)

    return records


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python discovery_riftbound.py "Set Name"')
    else:
        discover(sys.argv[1])