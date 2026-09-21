"""
Batch-tests discovery_fab.py against a maintained list of real FAB booster
sets (sourced from fabrary.net), since FAB's own site tagging (product_type)
is too inconsistent to auto-discover the full set list reliably.

Usage:
    python src/test_all_fab_sets.py
"""

from discovery_fab import (
    fetch_product, extract_release_date, extract_product_image,
    extract_box_contents, extract_pack_contents,
)

KNOWN_FAB_SETS = [
    "Welcome to Rathe", "Arcane Rising", "Crucible of War", "Monarch Unlimited",
    "Tales of Aria", "Everfest", "Uprising", "Dynasty", "Outsiders",
    "Dusk till Dawn", "Bright Lights", "Heavy Hitters", "Part the Mistveil",
    "Rosetta", "The Hunted", "High Seas", "Compendium of Rathe",
    "Omens of the Third Age", "iar",  # "iar" = Usurp the Shadow Throne's real internal title on FAB's site
]


def run():
    print(f"Testing {len(KNOWN_FAB_SETS)} known FAB sets.\n")

    missing_release_date = []
    missing_image = []
    missing_contents = []
    not_found = []

    for set_name in KNOWN_FAB_SETS:
        product = fetch_product("fab", set_name)
        if not product:
            not_found.append(set_name)
            print(f"{set_name}  [NOT FOUND]")
            continue

        title = product.get("title", {}).get("rendered", "UNKNOWN")
        html_content = product.get("content", {}).get("rendered", "")

        release_date = extract_release_date(html_content)
        image_url = extract_product_image(product)
        box_contents = extract_box_contents(html_content)
        pack_config = extract_pack_contents(html_content)

        status = []
        if not release_date:
            missing_release_date.append(set_name)
            status.append("NO DATE")
        if not image_url:
            missing_image.append(set_name)
            status.append("NO IMAGE")
        if not box_contents:
            missing_contents.append(set_name)
            status.append("NO CONTENTS")

        flag = f"  [{', '.join(status)}]" if status else ""
        print(f"{title}{flag}")

    print(f"\n--- Summary ---")
    print(f"Total sets tested: {len(KNOWN_FAB_SETS)}")
    print(f"Not found at all: {len(not_found)} -> {not_found}")
    print(f"Missing release date: {len(missing_release_date)} -> {missing_release_date}")
    print(f"Missing image: {len(missing_image)} -> {missing_image}")
    print(f"Missing box contents: {len(missing_contents)} -> {missing_contents}")


if __name__ == "__main__":
    run()