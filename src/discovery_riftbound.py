"""
Discovery script for Riot merch store product catalog (gzip file).

Confirmed working endpoint (15 Sep 2026):
    https://merch.riotgames.com/productPreviews/en-us.json.gz

Contents info lives as marketing text on each product's page HTML,
not a clean structured field — this script parses that text with
regex to pull out packs-per-display and a rough per-pack breakdown.
"""

import gzip
import json
import re
import requests
from bs4 import BeautifulSoup

URL = "https://merch.riotgames.com/productPreviews/en-us.json.gz"


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
    """Riftbound's contents info is a marketing sentence, not a clean
    list. Pull out the packs-per-display count and per-pack breakdown
    using regex against known phrasing patterns."""
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
            continue  # skip the "24 booster packs" sentence itself
        result["pack_breakdown"].append(f"{count} {label_clean}")

    return result


def discover():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    })

    print(f"Downloading {URL} ...")
    resp = session.get(URL, timeout=20)
    resp.raise_for_status()
    decompressed = gzip.decompress(resp.content)
    data = json.loads(decompressed)

    riftbound_items = {
        k: v for k, v in data.items()
        if (v.get("brand") or {}).get("slug") == "riftbound"
    }

    booster_items = {
        k: v for k, v in riftbound_items.items()
        if "booster" in v.get("title", "").lower()
        or "display" in v.get("title", "").lower()
    }
    print(f"Booster/display items found: {len(booster_items)}")

    for sku, item in booster_items.items():
        slug = item.get("slug")
        title = item.get("title")
        print(f"\n=== {title} ===")
        print(f"Image: {item.get('main_image', {}).get('src')}")
        print(f"Created: {item.get('_createdAt')}")

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
                print(f"Packs per display: {parsed['packs_per_display']}")
                print("Pack breakdown:")
                for pb in parsed["pack_breakdown"]:
                    print(f"  - {pb}")
            else:
                print("No contents line found on this page.")
        except requests.exceptions.RequestException as e:
            print(f"Failed to fetch page: {e}")


if __name__ == "__main__":
    discover()