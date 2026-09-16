"""
Shared data structure for pulled sealed product data.

Every game's discovery script should build one of these dicts per
product, so downstream code (validation, saving, pushing) can work
the same way regardless of which game the data came from.
"""


def make_product_record(
    game,
    title,
    sku,
    release_date,
    image_url,
    contents,
    price=None,
    source_url=None,
):
    """Build a standard product record.

    Args:
        game: short game key, e.g. "fab" or "riftbound"
        title: product name as found on the source
        sku: unique identifier from the source (SKU, post ID, etc.)
        release_date: string date if known, else None
        image_url: direct URL to a product image, else None
        contents: list of strings describing what's inside, else []
        price: optional numeric price if available
        source_url: optional URL of the page this was pulled from
    """
    return {
        "game": game,
        "title": title,
        "sku": sku,
        "release_date": release_date,
        "image_url": image_url,
        "contents": contents or [],
        "price": price,
        "source_url": source_url,
    }


def print_product_record(record):
    """Pretty-print a standard product record the same way for any game."""
    print(f"\n=== [{record['game']}] {record['title']} ===")
    print(f"SKU: {record['sku']}")
    print(f"Release date: {record['release_date']}")
    print(f"Image: {record['image_url']}")
    print(f"Price: {record['price']}")
    print("Contents:")
    if record["contents"]:
        for line in record["contents"]:
            print(f"  - {line}")
    else:
        print("  (none found)")