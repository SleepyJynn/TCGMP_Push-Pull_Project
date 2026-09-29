"""
Tests the actual user-facing flow: "type a product name, get a match" —
across every real product in the catalog, not just one hand-picked example
(the World's Strongest Warriors / OP-17 case we manually caught earlier).

Approach: pull the full live catalog once, then for every single product,
simulate what a real user would type (its title, but with curly quotes/
apostrophes replaced by the straight ones an ordinary keyboard produces —
exactly the mismatch that silently broke the OP-17 search before we fixed
normalize_text) and confirm search_products() actually finds that product.

This directly tests normalize_text() against every apostrophe/quote
variant that actually exists in the live catalog, not just the one case we
happened to notice by hand. It also flags any search that matches more than
one product, since discover() only ever returns the first match — an
ambiguous title means a real user's exact-name search could silently
return the wrong product.

Run: python test_search_all_products.py
"""

from discovery_onepiece import get_all_products, search_products, REGIONS, prompt_region

# Curly/smart punctuation the site might use, and the plain-keyboard
# character a real user would actually type instead.
_KEYBOARD_EQUIVALENTS = {
    "’": "'",   # ’ -> '
    "‘": "'",   # ‘ -> '
    "“": '"',   # “ -> "
    "”": '"',   # ” -> "
    "–": "-",   # – -> -
    "—": "-",   # — -> -
}


def as_typed(title):
    """What a user typing this title on a plain keyboard would produce."""
    for smart, plain in _KEYBOARD_EQUIVALENTS.items():
        title = title.replace(smart, plain)
    return title


def run_check(base_url, region_label):
    print(f"\n=== {region_label} ({base_url}) ===")
    products = get_all_products(base_url, subcategory="all")
    print(f"Testing search against {len(products)} real products...")

    self_find_failures = []
    ambiguous = []

    for p in products:
        typed = as_typed(p["title"])
        matches = search_products(products, typed)
        urls = [m["url"] for m in matches]

        if p["url"] not in urls:
            self_find_failures.append((p, typed))
        elif len(matches) > 1:
            ambiguous.append((p, matches))

    print(f"\n  Self-find check: {len(products) - len(self_find_failures)}/{len(products)} products "
          f"found by searching their own (keyboard-typed) title")
    if self_find_failures:
        print(f"  [FAIL] {len(self_find_failures)} products were NOT found by their own title — "
              f"normalize_text() is missing a real character variant:")
        for p, typed in self_find_failures[:15]:
            print(f"    - real title:  {p['title']!r}")
            print(f"      typed as:    {typed!r}")
    else:
        print("  [OK] every product is findable by its own title")

    print(f"\n  Ambiguity check: {len(ambiguous)} exact-title searches matched more than one product "
          f"(discover() only returns the first, so this could return the wrong one)")
    for p, matches in ambiguous[:10]:
        print(f"    - searching {p['title']!r} also matched:")
        for m in matches:
            if m["url"] != p["url"]:
                print(f"        · {m['title']!r} ({m['url']})")

    return products


if __name__ == "__main__":
    region_choice = prompt_region()
    if not region_choice:
        print("Invalid region selected.")
    else:
        region_label, region_base_url = region_choice
        run_check(region_base_url, region_label)