import re
import sys
import requests

from common import make_product_record, print_product_record

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})

SETS_URL = "https://api.scryfall.com/sets"

BOX_TYPES = {
    "1": ("Play Booster Box", "play-booster-box", ""),
    "2": ("Collector Booster Box", "collector-booster-box", "collector"),
    "3": ("Draft Booster Box", "draft-booster-box", "draft"),
    "4": ("Set Booster Box", "set-booster-box", "set"),
}


def fetch_all_sets():
    resp = session.get(SETS_URL, timeout=15, headers={"Accept": "application/json"})
    return resp.json().get("data", [])


def find_set(sets, search_term):
    search_lower = search_term.lower()
    exact_matches = [s for s in sets if s.get("name", "").lower() == search_lower]
    if exact_matches:
        paper_matches = [s for s in exact_matches if not s.get("digital")]
        return paper_matches[0] if paper_matches else exact_matches[0]
    substring_matches = [s for s in sets if search_lower in s.get("name", "").lower()]
    paper_substring = [s for s in substring_matches if not s.get("digital")]
    if paper_substring:
        return paper_substring[0]
    return substring_matches[0] if substring_matches else None


def build_scg_slug(set_name, box_suffix, set_code, code_fragment):
    slug_name = set_name.lower().replace(":", "").replace("'", "").strip().replace(" ", "-")
    return f"{slug_name}-{box_suffix}-sld-mtg-bbx-{set_code}{code_fragment}-en"


def fetch_scg_page_html(slug):
    url = f"https://starcitygames.com/{slug}/"
    resp = session.get(url, timeout=10)
    resp.raise_for_status()
    return resp.text, url


def extract_scg_image(page_html):
    match = re.search(r'<meta property="og:image" content="([^"]+)"', page_html)
    return match.group(1) if match else None


def extract_scg_contents(page_html):
    text = re.sub(r"<[^>]+>", " ", page_html)
    text = text.replace("&nbsp;", " ").replace("\\n", " ")
    text = re.sub(r"\s+", " ", text)
    match = re.search(r"(Each factory-sealed[^.]*\.)", text, re.IGNORECASE)
    return [match.group(1).strip()] if match else []


def prompt_box_type():
    print("\nWhich box type?")
    for key, (label, _, _) in BOX_TYPES.items():
        print(f"  {key}. {label}")
    choice = input("Enter number: ").strip()
    return BOX_TYPES.get(choice)


def discover(search_term):
    sets = fetch_all_sets()
    match = find_set(sets, search_term)

    if not match:
        print(f"No Scryfall set found matching '{search_term}'")
        return

    print(f"[matched Scryfall set] name='{match.get('name')}' code='{match.get('code')}' "
          f"set_type='{match.get('set_type')}' digital={match.get('digital')}")

    box_choice = prompt_box_type()
    if not box_choice:
        print("Invalid box type selected.")
        return
    box_label, box_suffix, code_fragment = box_choice

    slug = build_scg_slug(match.get("name", ""), box_suffix, match.get("code", ""), code_fragment)

    image_url = None
    contents = []
    source_url = None
    try:
        page_html, source_url = fetch_scg_page_html(slug)
        image_url = extract_scg_image(page_html)
        contents = extract_scg_contents(page_html)
    except requests.exceptions.RequestException as e:
        print(f"(Couldn't fetch StarCityGames page for '{slug}': {e})")

    record = make_product_record(
        game="Magic: The Gathering",
        title=f"{match.get('name')} - {box_label}",
        sku=match.get("code"),
        release_date=match.get("released_at"),
        image_url=image_url or match.get("icon_svg_uri"),
        contents=contents if contents else ["(contents not found)"],
        source_url=source_url,
    )
    print_product_record(record)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python discovery_mtg.py "Set Name"')
    else:
        discover(sys.argv[1])