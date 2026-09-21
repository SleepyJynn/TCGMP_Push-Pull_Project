"""
Discovery script for TCG sealed product data — multi-game ready.

Purpose: fetch a product by name from any configured game's source,
then parse it into a standard product record (see common.py).

Each entry in GAME_SOURCES is a confirmed, working data source found
via browser DevTools (see project notes). Only add a game here once
its endpoint has actually been confirmed working — don't guess URLs.

Usage:
    python src/discovery_fab.py "Outsiders"
"""

import sys
import re
import requests
from bs4 import BeautifulSoup
from common import make_product_record, print_product_record

GAME_SOURCES = {
    "fab": {
        "name": "Flesh and Blood",
        "search_url": "https://fabtcg.com/api/wp/v2/product",
        "search_param": "search",
    },
}

def fetch_product(game_key, search_term):
    if game_key not in GAME_SOURCES:
        print(f"Unknown game '{game_key}'. Configured games: {list(GAME_SOURCES.keys())}")
        return None

    source = GAME_SOURCES[game_key]
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://fabtcg.com/",
    })

    resp = session.get(
        source["search_url"],
        params={source["search_param"]: search_term},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        print(f"No results found for '{search_term}' in {source['name']}")
        return None

    search_lower = search_term.lower()
    exact_matches = [r for r in results if r.get("title", {}).get("rendered", "").lower() == search_lower]
    if exact_matches:
        return exact_matches[0]
    return results[0]


def extract_release_date(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text(separator=" ", strip=True)

    patterns = [
        r"Release date:\s*([A-Za-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})",
        r"In Stores\s*([A-Za-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def extract_product_image(product_json):
    try:
        return product_json["yoast_head_json"]["og_image"][0]["url"]
    except (KeyError, IndexError, TypeError):
        return None


def extract_pack_contents(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    contents = []

    for heading in soup.find_all(["h4", "summary"]):
        heading_text = heading.get_text(strip=True).lower()
        if "pack configuration" in heading_text or "rarity distribution" in heading_text:
            next_ul = heading.find_next("ul")
            if next_ul:
                contents.extend(li.get_text(strip=True) for li in next_ul.find_all("li"))

    return contents


def extract_box_contents(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text(separator=" ", strip=True)

    patterns = [
        r"[Aa] booster display contains (\d+) booster packs",
        r"Booster Display\s*\((\d+)\s*packs\)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return [f"{match.group(1)} booster packs per display"]
    return []

def discover(game_key, search_term):
    product = fetch_product(game_key, search_term)
    if not product:
        return None

    title = product.get("title", {}).get("rendered", "UNKNOWN")
    html_content = product.get("content", {}).get("rendered", "")

    # debug_text = BeautifulSoup(html_content, "html.parser").get_text(separator=" ", strip=True)
    # print("\n[DEBUG] Lines mentioning 'pack':")
    # for sentence in re.split(r"(?<=[.!?])\s+", debug_text):
    #     if "pack" in sentence.lower():
    #         print(f"  - {sentence.strip()}")

    record = make_product_record(
        game=game_key,
        title=title,
        sku=product.get("id"),
        release_date=extract_release_date(html_content),
        image_url=extract_product_image(product),
        contents=extract_box_contents(html_content),
        pack_configuration=extract_pack_contents(html_content),
        source_url=product.get("link"),
    )
    print_product_record(record)
    return record


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python src/discovery_fab.py "<product name>"')
    else:
        discover("fab", sys.argv[1])