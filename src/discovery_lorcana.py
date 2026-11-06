"""
Discovery script for Disney Lorcana TCG sealed products (Ravensburger).

Source: the official site, https://www.disneylorcana.com/en-US
  - The home page's Products menu links one page per set / product
    (/en-US/product/<slug>), confirmed from a live diagnostic run on
    14 Oct 2026 (lorcana_diag.py): 25 slugs.
  - The pages are server-rendered Nuxt HTML. The __NUXT_DATA__ payload is the
    same ~37k site-wide blob on every page (menu data), NOT product data, so
    everything is read from the rendered HTML between the last </nav> and
    <footer>.
  - There is no product list / API with structured fields. Each set page
    shows its products as images whose alt text names them ("Box of
    Shimmering Skies booster packs", "Starter decks for Azurite Sea",
    "Illumineer's Trove for ...", "Stitch Gift Set ..."), next to a heading
    and marketing text. Products are found from those images; contents come
    from the matching heading's text (its <li> list when there is one).

Scope (agreed 14 Oct 2026):
  - ALL official products on the site, accessories included (sleeves, deck
    boxes, playmats, portfolios).
  - Excluded: "Disney Lorcana Books" and the Companion App (not TCG product).
  - Not-yet-released products are skipped (same rule as Grand Archive):
    Hyperia City, Cosmic Quest, Into the Inkdark as of 14 Oct 2026. They are
    picked up automatically once their date has passed.

Release dates: newer pages print them ("Prerelease: July 17, 2026
Everywhere: July 24, 2026", "In Hobby Stores: 8/9/24 Everywhere: 8/23/24",
"available at Hobby Stores ... on May 17, 2024 and at Mass retailers ... on
May 31, 2024", "Available October 2, 2026"). Pages without one fall back to
_RELEASE_DATES below (official announcements, each with a source). The
"Release date" printed is the WIDE ("Everywhere" / mass retail) date; the
prerelease / hobby date is shown in the release note.

Usage:
    python src/discovery_lorcana.py all --out=lorcana_output.txt
    python src/discovery_lorcana.py "Azurite" --debug --out=lorcana_debug.txt
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

import html as html_lib
import re
import time
from datetime import date, datetime

import requests
from common import make_product_record, print_product_record

BASE = "https://www.disneylorcana.com"
HOME = BASE + "/en-US"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
})

EXCLUDED_SLUGS = {"books"}  # Disney Lorcana Books -- novels/guides, out of scope

# Pages that are ONE product (not a set page with several products).
STANDALONE = {
    "collection-starter-set", "scrooge-mcduck-gift-box", "elsa-gift-box", "2-player-starter-set",
    "gateway", "deep-trouble", "palace-heist", "great-hunny-rescue",
}

# Not released, and no exact date on the page to test against.
UPCOMING = {
    "cosmic-quest": "page says 'Coming Q2 2027'",
    "into-the-inkdark": "page says 'Prelease Feb 5 Release Feb 12' with no year (next set after Hyperia City, Oct 2026)",
}

# ---------------------------------------------------------------------------
# Release dates for pages that don't print one (wide date, prerelease/hobby
# date or None, source). All from official announcements unless noted.
# ---------------------------------------------------------------------------
_RELEASE_DATES = {
    "the-first-chapter": ("2023-09-01", "2023-08-18",
                          "https://articles.starcitygames.com/lorcana/disneys-lorcana-tcg-announces-official-release-dates-for-the-first-chapter-starter-decks-and-more/"),
    "rise-of-the-floodborn": ("2023-12-01", "2023-11-17",
                              "https://blogmickey.com/2023/09/first-look-at-disney-lorcanas-second-set-rise-of-the-floodborn-cards-products-and-launch-dates/"),
    "gateway": ("2024-08-09", None,
                "https://www.laughingplace.com/w/entertainment/disney-lorcana-tcg-gateway-august-2024/"),
    "archazias-island": ("2025-03-21", "2025-03-07", "https://www.cgmagonline.com/news/disney-lorcana-2025-roadmap/"),
    "reign-of-jafar": ("2025-06-06", "2025-05-30",
                       "https://press-start.com.au/news/2025/05/09/exclusive-card-reveal-disney-lorcanas-reign-of-jafar-is-almost-here/"),
    "fabled": ("2025-09-05", "2025-08-29",
               "https://www.disneylorcana.com/en-US/news/2025/08/2025-8-fabled-prerelease-primer"),
    "whispers": ("2025-11-14", "2025-11-07",
                 "https://www.disneylorcana.com/en-US/news/2025/10/2025-10_whispers-in-the-well-press-release"),
    "elsa-gift-box": ("2025-10-03", None,
                      "Disney Lorcana tracker v18 (Oct 3 2025); gamingtrend.com 8 Oct 2025 says 'now available everywhere'"),
    "winterspell": ("2026-02-20", "2026-02-13",
                    "https://www.disneylorcana.com/en-US/news/2026/01/2026-01-winterspell-press-release"),
    "scrooge-mcduck-gift-box": ("2026-03-13", None,
                                "https://www.disneylorcana.com/en-US/news/2025/12/2025-12-december-product-announcement"),
    "2-player-starter-set": ("2026-05-08", None,
                             "https://www.disneylorcana.com/en-US/news/2025/12/2025-12-december-product-announcement"),
    "collection-starter-set": ("2026-09-04", None,
                               "https://icv2.com/articles/news/view/62632/ravensburger-will-release-new-disney-lorcana-collection-starter-set"),
}
# Product-level overrides (lowercase words that must all appear in the name).
_PRODUCT_DATES = [
    (["collection starter set", "stitch"], "2026-03-13", None,
     "https://www.disneylorcana.com/en-US/news/2025/12/2025-12-december-product-announcement"),
]

# Contents for products whose disneylorcana.com page shows only an image and a
# title (checked 21 Oct 2026). Used ONLY when nothing was found on the page;
# every entry carries the source it was copied from. Keyed by product name.
_CONTENTS_FALLBACK = {
    "Whispers in the Well Starter Decks": (
        ["Includes: 1 deck of 60 cards, including 2 foil cards; 11 paper damage counters; 1 paper playmat; "
         "1 paper tracker token; 1 game rules; 1 booster pack of 12 randomized cards"],
        "https://www.outland.no/p-whispers-in-the-well-amber-emerald-simba-megara-starter-deck-whispers-in-the-well-lorcana-tcg-disney-4050368900029"),
    "Whispers in the Well Illumineer's Trove": (
        ["Includes: 1 card storage box; 6 foiled ink-themed card dividers; 8 Whispers in the Well booster packs; "
         "6 glow-in-the-dark damage-counter dice; 1 foiled lore counter"],
        "https://wiki.mushureport.com/wiki/Whispers_in_the_Well_Illumineer%27s_Trove (cites the official product page)"),
    "Archazia's Island Starter Decks": (
        ["Includes: 1 deck of 60 cards, including 2 foil cards; 11 damage counters; 1 paper playmat; "
         "1 paper tracker token; 1 quick start rulebook; 1 bonus booster pack of 12 randomized cards"],
        "https://www.outland.no/p-archazias-island-amethyst-steel-iago-jafar-starter-deck-archazias-island-lorcana-tcg-disney-4050368984814"),
    "Archazia's Island Illumineer's Trove": (
        ["Includes: 1 card storage box; 6 ink-themed card dividers; 8 Archazia's Island booster packs; "
         "6 damage-counter dice; 1 lore counter"],
        "https://wiki.mushureport.com/wiki/Archazia%27s_Island_Illumineer%27s_Trove (cites the official product page); "
        "6 dividers also per https://bardsandcards.com/products/disney-lorcana-archazias-island-illumineers-trove "
        "(lorcana.gg says 7)"),
    "Archazia's Island Lilo Gift Set": (
        ["Includes: 5 Disney Lorcana booster packs (assorted, from earlier sets); "
         "1 exclusive 'Lilo - Escape Artist' Glimmer Foil promo card; 1 Lilo & Stitch storage box (holds up to 250 cards); "
         "3 punch-out card dividers"],
        "https://hmv.com/store/pop-culture/toys-games/archazia-s-island-gift-set-disney-lorcana-trading; "
        "https://laughingplace.com/w/disney-merchandise/ravensburger-details-for-archazias-island"),
    "Archazia's Island Card Sleeves & Deck Boxes": (
        ["Includes: 65 card sleeves per pack; deck box holds up to 80 sleeved cards"],
        "https://totalcards.net/products/lorcana-archazias-island-cinderella-card-sleeves-65-sleeves; "
        "https://facetofacegames.com/products/disney-lorcana-deck-box-80-archazias-island-cinderella-ballroom-sensation"),
}

# ---------------------------------------------------------------------------
# Product types, matched against an image's alt text + file path (lowercase).
# First match wins, so specific types come before generic ones.
# ---------------------------------------------------------------------------
_TYPES = [
    ("Collection Starter Set", r"collection starter set"),
    ("2-Player Starter Set", r"two player starter|2-player starter"),
    ("Illumineer's Quest", r"illumineer.s quest|\biq\d\b|/iq\d/"),
    ("Illumineer's Trove", r"trove"),
    ("Prerelease Box", r"pre-?release box|/pre-release/"),
    ("Gift Set", r"gift (?:set|box)|giftset|gift-set"),
    ("Collector's Edition", r"collectors? edition|collector.s edition"),
    ("Collector Boosters", r"collector booster"),
    ("Starter Decks", r"starter deck|single-player deck|deck boxes from|starters|starter_deck"),
    ("Booster Packs & Display Box", r"booster|display packs|booster-display"),
    ("Portfolios", r"portfolio"),
    ("Card Sleeves & Deck Boxes", r"sleeves|accessories"),
]
_NOT_PRODUCT_RE = re.compile(
    r"\bcards?\b(?!\s+(?:game|sleeves|portfolio|case))|glimmer|\bapp\b|logo|header|background|decorative|prize|video|thumbnail of|teaser|"
    r"holder image|lifestyle|key art|franchis", re.I)
_STANDALONE_IMG_RE = re.compile(
    r"product image|components|product shot|box photo|photo of the|contents of|spill|gift-set_en|collection-product",
    re.I)


def classify(text):
    low = (text or "").lower()
    for name, rx in _TYPES:
        if re.search(rx, low):
            return name
    return None


# ---------------------------------------------------------------------------
# Fetching / page splitting
# ---------------------------------------------------------------------------
def _get(url, attempts=3):
    for i in range(attempts):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            return r
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if i == attempts - 1:
                raise
            time.sleep(2 * (i + 1))


def clean(fragment):
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def own_content(page):
    """Rendered page body without the site menu and footer: everything after
    the last </nav> before <footer> (confirmed on 4 raw pages, 14 Oct 2026)."""
    foot = page.find("<footer")
    foot = foot if foot != -1 else len(page)
    navs = [m.end() for m in re.finditer(r"</nav>", page[:foot])]
    body = page[navs[-1] if navs else 0:foot]
    return re.sub(r"<(script|style|svg)\b[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)


def fetch_menu():
    """[(slug, menu name)] from the home page's Products menu, in menu order."""
    home = _get(HOME).text
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(?:https://www\.disneylorcana\.com)?/en-US/product_?/([a-z0-9\-]+)/?"[^>]*>(.*?)</a>',
                         home, re.S):
        slug = m.group(1)
        name = clean(m.group(2))
        if slug in seen or not name:
            continue
        seen.add(slug)
        out.append((slug, name))
    return out


# ---------------------------------------------------------------------------
# Release dates
# ---------------------------------------------------------------------------
_MONTH_DATE = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
_PAGE_DATE_PATTERNS = [
    re.compile(rf"Prerelease:\s*({_MONTH_DATE})\s*Everywhere:\s*({_MONTH_DATE})"),
    re.compile(r"(?:In Hobby Stores|Hobby Store Release Date):\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*Everywhere:\s*(\d{1,2}/\d{1,2}/\d{2,4})"),
    re.compile(rf"available at Hobby Stores[^.]*? on ({_MONTH_DATE}),? and at Mass retailers[^.]*? on ({_MONTH_DATE})", re.I),
]
_SINGLE_DATE_RE = re.compile(rf"\bAvailable\s+({_MONTH_DATE})")


def to_iso(raw):
    raw = re.sub(r"(\d)(st|nd|rd|th)", r"\1", raw).replace(",", "").strip()
    for fmt in ("%B %d %Y", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def page_release(text):
    """(wide_iso, early_iso, how) from the page's own text, or (None, None, None)."""
    for rx in _PAGE_DATE_PATTERNS:
        m = rx.search(text)
        if m:
            return to_iso(m.group(2)), to_iso(m.group(1)), f"on the product page: \"{m.group(0)[:120]}\""
    m = _SINGLE_DATE_RE.search(text)
    if m:
        return to_iso(m.group(1)), None, f"on the product page: \"{m.group(0)}\""
    return None, None, None


# ---------------------------------------------------------------------------
# Contents
# ---------------------------------------------------------------------------
_NUM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty-four|sixty)"
# Longer items first: alternation stops at the first match, so "card" must
# not come before "card sleeves" / "card portfolio" (it cut "65 matte-finish
# card sleeves" down to "65 matte-finish card").
_ITEM_WORDS = (r"booster packs?|boosters|card sleeves|card portfolios?|card dividers?|card storage box|"
               r"deck boxes|deck box|storage box(?:es)?|lore trackers?|lore counters?|damage dice|"
               r"packs|sleeves|dice|dividers?|tokens?|counters?|portfolios?|rulebooks?|guides?|playmats?|"
               r"postcards?|posters?|booklets?|binders?|coins?|stickers?|cards?|decks?")
_NUM_PHRASE_RE = re.compile(rf"\b{_NUM}\s+(?:[\w’'“”\"&.-]+\s){{0,5}}?(?:{_ITEM_WORDS})\b", re.I)


def li_items(fragment):
    return [clean(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", fragment, re.S) if clean(x)]


def contents_from(fragment, ptype=None):
    """Short contents lines from a product's HTML/text fragment."""
    lines = []
    items = li_items(fragment)
    if items:
        lines.append("Includes: " + "; ".join(items))
    else:
        text = clean(fragment)
        found = []
        for m in _NUM_PHRASE_RE.finditer(text):
            ph = m.group(0).strip()
            if re.search(r"(?:up to|holds?|fits?)\s*$", text[max(0, m.start() - 12):m.start()], re.I):
                # Capacity, not contents ("holds up to 250 sleeved cards") --
                # kept, but labelled, since for deck boxes / portfolios / storage
                # boxes the capacity IS the useful spec.
                ph = "holds up to " + ph
            if ph.lower() not in (f.lower() for f in found):
                found.append(ph)
        # "...including eight booster packs, storage box, lore counter, and six
        # damage dice." -- the numbers alone dropped the storage box and lore
        # counter (Wilds Unknown Trove), so a full "including a, b, and c"
        # list wins when it contains the counted items.
        inc = re.search(r"\bincluding\s+([^.;:]+)", text, re.I)
        if inc and found:
            parts = [re.sub(r"^(?:even|also|plus)\s+(?:an?\s+)?", "", p.strip(), flags=re.I)
                     for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", inc.group(1)) if p.strip()]
            if len(parts) >= 3 and all(any(f.lower() in p.lower() for p in parts) for f in found):
                found = parts
        if found:
            lines.append("Includes: " + "; ".join(found))
        elif text:
            first = re.split(r"(?<=[.!?])\s", text)[0]
            # Drop an ALL-CAPS section heading glued to the front
            # ("ESSENTIAL SURVIVAL MATERIALS The more glimmers ...").
            first = re.sub(r"^(?:[A-Z0-9’'&!,.:-]+\s+){2,}(?=[A-Z][a-z])", "", first)
            # A bare product title ("Illumineer's Trove") is not a description:
            # Archazia's Island's Featured Products tabs carry only image + title.
            if len(first) >= 40 and re.search(r"[.!?]$", first):
                lines.append("Description: " + first[:250])
    if ptype == "Booster Packs & Display Box":
        joined = " ".join(lines).lower() + " " + clean(fragment).lower()
        has_pack = re.search(r"\b12\b|twelve", joined)
        has_box = re.search(r"\b24\b|twenty-four", joined)
        if not (has_pack and has_box):
            lines.append("Pack configuration (Lorcana standard, NOT stated on this page): 12 cards per pack, "
                         "24 packs per booster display")
    return lines


def heading_blocks(body):
    """[(heading text, html until the next heading)] for h1-h3 in the body."""
    heads = list(re.finditer(r"<(h[1-3])[^>]*>(.*?)</\1>", body, re.S))
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        out.append((clean(h.group(2)), body[h.end():end]))
    return out


def best_block(blocks, ptype):
    """The heading block about this product type with the most text."""
    cands = [(len(clean(frag)), title, frag) for title, frag in blocks if classify(title) == ptype]
    if not cands:
        return None, None
    _, title, frag = max(cands)
    return title, frag


def accessory_products(body):
    """Old set pages (The First Chapter .. Ursula's Return) describe accessories
    under an ACCESSORIES heading with bold labels -- 'Card Sleeves', 'Deck
    Boxes', 'Playmats', 'Portfolios' -- and no images (confirmed on the raw
    The First Chapter page). Each label becomes a product."""
    m = re.search(r"<h[1-3][^>]*>\s*ACCESSORIES\s*</h[1-3]>(.*?)(?=<h[1-3][^>]*>|$)", body, re.S | re.I)
    if not m:
        return []
    parts = re.split(r"<p[^>]*>\s*<(?:strong|b)>(.*?)</(?:strong|b)>\s*</p>", m.group(1))
    out = []
    for i in range(1, len(parts) - 1, 2):
        label, desc = clean(parts[i]), parts[i + 1]
        if label:
            out.append((label, desc))
    return out


# ---------------------------------------------------------------------------
# Page -> products
# ---------------------------------------------------------------------------
def page_images(body):
    out = []
    for tag in re.findall(r"<img[^>]*>", body):
        src = re.search(r'src="([^"]+)"', tag)
        if not src or "ravensburger.cloud" not in src.group(1):
            continue
        alt = re.search(r'alt="([^"]*)"', tag)
        out.append((src.group(1), html_lib.unescape(alt.group(1)) if alt else ""))
    return out


def products_from_page(slug, name, page):
    body = own_content(page)
    text = clean(body)
    imgs = page_images(body)
    blocks = heading_blocks(body)
    products = []

    if slug in STANDALONE:
        img = next((s for s, a in imgs if _STANDALONE_IMG_RE.search(a + " " + s)), None)
        if not img:
            short = name.split(":")[-1].strip().lower()  # "Illumineer's Quest: Deep Trouble" -> "deep trouble"
            img = next((s for s, a in imgs if (name.lower() in a.lower() or a.strip().lower() == short)
                        and not _NOT_PRODUCT_RE.search(a)), None)
        if not img:
            img = next((s for s, a in imgs if classify(a + " " + s) and not _NOT_PRODUCT_RE.search(a)), None)
        # Contents: the list after an "includes / contents / what you'll find" heading or line.
        frag = None
        for title, f in blocks:
            if re.search(r"includes|contents|what you.ll find", title, re.I) and (li_items(f) or clean(f)):
                frag = f
                break
        if frag is None:
            m = re.search(r"(?:includes:|contents include:|contents|what you.ll find inside:)(.*)", body, re.S | re.I)
            frag = m.group(1)[:4000] if m else body
        edition = re.search(r"COLLECTION STARTER SET:\s*([A-Z ]+EDITION)", text)
        title = f"{name}: {edition.group(1).title()}" if edition else name
        products.append({"name": title, "type": classify(name) or name, "image": img,
                         "contents": contents_from(frag, classify(name))})
        return products

    seen_types = set()
    for src, alt in imgs:
        ptype = classify(alt + " " + src.split("/cms/gallery/")[-1])
        if not ptype or ptype in ("Illumineer's Quest", "Collector Boosters") or _NOT_PRODUCT_RE.search(alt):
            continue
        if ptype in seen_types:
            continue
        seen_types.add(ptype)
        if ptype == "Collection Starter Set":
            ed = re.search(r"(\w+) Edition", alt)
            pname = f"Collection Starter Set: {ed.group(1)} Edition" if ed else "Collection Starter Set"
        elif ptype == "2-Player Starter Set":
            pname = "2-Player Starter Set"
        elif ptype == "Gift Set":
            g = re.search(r"([A-Z][\w']+(?: [A-Z][\w']+)?) Gift (?:Set|Box)", alt)
            who = g.group(1) if g else ""
            # "Lorcana Into The Inklands Gift Set package" gave "The Inklands" --
            # a captured word that is part of the set name is not a character.
            if not who or who.lower() in ("lorcana", "disney") or any(w in name.lower() for w in who.lower().split()):
                pname = f"{name} Gift Set"
            else:
                pname = f"{name} {who} Gift Set"
        elif ptype == "Collector's Edition":
            pname = f"{name} Disney100 Collector's Edition" if "100" in alt else f"{name} Collector's Edition"
        else:
            pname = f"{name} {ptype}"
        _, frag = best_block(blocks, ptype)
        # FIXED 16 Oct 2026 (first full run): 9 products came back with no
        # contents -- on newer pages (Archazia's Island, Fabled, Whispers in
        # the Well, Wilds Unknown) the product's text sits next to its image
        # rather than under a heading that names the product. The slice from
        # this image to the next one is now also tried, and whichever of the
        # two gives an actual list / numbers wins.
        near = _text_near_image(body, src)
        products.append({"name": pname, "type": ptype, "image": src,
                         "contents": _best_contents([frag, near], ptype)})

    accessories = accessory_products(body)
    if accessories:
        # FIXED 16 Oct 2026: the older pages (Rise of the Floodborn, Into the
        # Inklands, Ursula's Return) ALSO have one shared accessories photo,
        # which became a separate "Card Sleeves & Deck Boxes" product
        # duplicating the per-accessory entries below. That photo is now used
        # as the image of each accessory instead.
        shared = next((p for p in products if p["type"] == "Card Sleeves & Deck Boxes"), None)
        products = [p for p in products if p is not shared]
        for label, desc in accessories:
            products.append({"name": f"{name} {label}", "type": "Accessory",
                             "image": shared["image"] if shared else None,
                             "image_shared": bool(shared), "contents": contents_from(desc)})
    return products


def _is_product_img(src, alt):
    return bool(classify(alt + " " + src.split("/cms/gallery/")[-1])) and not _NOT_PRODUCT_RE.search(alt)


def _text_near_image(body, src):
    """The text that belongs to a product photo.

    FIXED 20 Oct 2026 (full run): reading forward from the photo to the next
    image gave the Wilds Unknown booster box the TROVE's text ("eight booster
    packs; six damage dice") -- on that page each module is heading + text +
    photo, so the text sits BEFORE its photo. Now: if the photo's own
    <section> holds only this one product photo, the whole section is its
    text (works whether the text is before or after). If the section holds
    several product photos (a carousel or tab strip, e.g. Attack of the Vine,
    Archazia's Island), read forward from the photo but stop at the next
    image or the end of the section -- so Archazia's sleeves no longer pick up
    the next section's "Dual-Ink Glimmers" text."""
    i = body.find(src)
    if i == -1:
        return ""
    start = body.rfind("<section", 0, i)
    end = body.find("</section>", i)
    if start != -1 and end != -1:
        sec = body[start:end]
        prods = [1 for s, a in page_images(sec) if _is_product_img(s, a)]
        if len(prods) == 1:
            # FIXED 21 Oct 2026 (v5 live run): the Wilds Unknown Trove photo
            # sits alone in its own small <section> with NO text, while its
            # "TREASURES TO SEE YOU THROUGH ... eight booster packs ... six
            # damage dice" block is in the section just before it -- so this
            # returned an empty section and the Trove had no contents. When the
            # photo's own section has no text, use the last heading block in
            # front of the photo instead (only if no other product photo sits
            # in between, so it can't take another product's text).
            if _has_body_text(sec):
                return sec
            before = _text_before_image(body, src)
            return before if _has_body_text(before) else sec
        # Several product photos in one section. If THIS photo has its own
        # heading + text in front of it inside the section, that is its text
        # (Wilds Unknown: "TREASURES TO SEE YOU THROUGH ... eight booster
        # packs ... six damage dice" then the Trove photo -- confirmed by the
        # 20 Oct 2026 debug run). Otherwise the text follows the photo (the
        # Attack of the Vine carousel: photo, then heading + text).
        # The layout is decided once per section, from its FIRST product
        # photo: text in front of it -> every photo's text is in front;
        # otherwise text follows each photo. (Deciding per photo gave the
        # Attack of the Vine booster box the Trove slide's text.)
        first = next(src_ for src_, a in page_images(sec) if _is_product_img(src_, a))
        # (A bare section title like "Featured Products" in front of the first
        # photo doesn't count -- Azurite Sea has one and its text follows.)
        if _has_body_text(_text_before_image(body, first, start)):
            return _text_before_image(body, src, start)
    after = _after_image(body, src)
    cut = after.find("</section>")
    return after[:cut] if cut != -1 else after


def _has_body_text(chunk):
    """True if the chunk has real text beyond its heading."""
    m = re.match(r"\s*<(h[1-3])[^>]*>.*?</\1>(.*)", chunk or "", re.S)
    rest = clean(m.group(2)) if m else clean(chunk)
    return len(rest) >= 40


def _text_before_image(body, src, section_start=0):
    """From the last heading before this photo (inside its section) up to the
    photo, if no other product photo sits in between."""
    i = body.find(src)
    if i == -1:
        return ""
    tag_start = body.rfind("<img", 0, i)
    heads = [m.start() for m in re.finditer(r"<h[1-3][^>]*>", body[:tag_start]) if m.start() >= section_start]
    if not heads:
        return ""
    chunk = body[heads[-1]:tag_start]
    if any(_is_product_img(s, a) for s, a in page_images(chunk)):
        return ""
    return chunk


def _after_image(body, src):
    i = body.find(src)
    if i == -1:
        return ""
    # Start AFTER the <img ...> tag -- starting at the src leaked the tag's own
    # attributes ('...png" alt="..." loading="lazy">') into the description
    # (16 Oct 2026, Archazia's Island debug run).
    end_tag = body.find(">", i)
    i = end_tag + 1 if end_tag != -1 else i + len(src)
    nxt = body.find("<img", i)
    return body[i:nxt if nxt != -1 else i + 4000][:4000]


def _best_contents(frags, ptype):
    """Contents from the fragment that yields a real list or counts; a bare
    'Description:' line only if nothing better is found."""
    results = [contents_from(f, ptype) for f in frags if f and clean(f)]
    def score(lines):
        joined = " ".join(lines)
        return (2 if "Includes:" in joined else 0) + (1 if lines and "Description: Previous" not in joined else 0)
    results = [r for r in results if r]
    if not results:
        return contents_from("", ptype)
    return max(results, key=score)


def product_release(slug, product_name, page_text):
    for words, wide, early, src in _PRODUCT_DATES:
        if all(w in product_name.lower() for w in words):
            return wide, early, f"product announcement -- {src}"
    wide, early, how = page_release(page_text)
    if wide:
        return wide, early, how
    if slug in _RELEASE_DATES:
        wide, early, src = _RELEASE_DATES[slug]
        return wide, early, f"not on the product page -- {src}"
    return None, None, None


# ---------------------------------------------------------------------------
def get_all_products():
    menu = fetch_menu()
    all_products, skipped = [], []
    for slug, name in menu:
        if slug in EXCLUDED_SLUGS:
            continue
        if slug in UPCOMING:
            skipped.append((name, UPCOMING[slug]))
            continue
        url = f"{HOME}/product/{slug}"
        try:
            page = _get(url).text
        except requests.exceptions.RequestException as e:
            print(f"  [WARN] could not fetch {url}: {e}")
            continue
        text = clean(own_content(page))
        for p in products_from_page(slug, name, page):
            wide, early, how = product_release(slug, p["name"], text)
            if wide and date.fromisoformat(wide) > date.today():
                skipped.append((p["name"], f"releases {wide}"))
                continue
            if not p["contents"] and p["name"] in _CONTENTS_FALLBACK:
                p["contents"], p["contents_source"] = _CONTENTS_FALLBACK[p["name"]]
            p.update({"slug": slug, "set_name": name, "release_date": wide, "early_date": early,
                      "release_how": how, "source_url": url, "_body": own_content(page)})
            all_products.append(p)

    # No duplicates: a product shown on more than one page (the 2-Player
    # Starter Set also appears on the Wilds Unknown page) is kept once,
    # preferring its own standalone page.
    unique, seen = [], {}
    for p in sorted(all_products, key=lambda x: x["slug"] not in STANDALONE):
        key = re.sub(r"[^a-z0-9]", "", p["name"].lower().split(":")[0] if p["type"] == "2-Player Starter Set"
                     else p["name"].lower())
        if p["type"] == "2-Player Starter Set":
            key = "2playerstarterset"
        if key in seen:
            continue
        seen[key] = True
        unique.append(p)
    order = {s: i for i, (s, _) in enumerate(menu)}
    unique.sort(key=lambda p: order.get(p["slug"], 999))
    return unique, skipped


def build_record(p):
    return make_product_record(
        game="Disney Lorcana",
        title=p["name"],
        sku=None,
        release_date=p["release_date"],
        image_url=p["image"],
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["source_url"],
    )


def print_notes(p):
    if p["release_date"]:
        early = f"; prerelease / hobby stores {p['early_date']}" if p["early_date"] else ""
        print(f"Release note: wide release{early} -- {p['release_how']}")
    else:
        print("Release note: NO DATE FOUND (not on the page and not in _RELEASE_DATES)")
    if p.get("contents_source"):
        print(f"Contents note: not on the product page -- {p['contents_source']}")
    if not p["image"] and p["type"] == "Accessory":
        # Checked 21 Oct 2026: no official photo anywhere (product page, news
        # posts, Mushu Report wiki). Each is a line of several designs, so a
        # single retailer photo would misrepresent it -- left blank on purpose.
        print("Image note: no official photo exists for this accessory line (several designs; "
              "left blank on purpose)")
    elif not p["image"]:
        print("Image note: no image for this product on the page")
    elif p.get("image_shared"):
        print("Image note: one shared photo of all this set's accessories (no separate image per accessory on the page)")


def discover_all():
    products, skipped = get_all_products()
    print(f"[DEBUG] Found {len(products)} Disney Lorcana products:")
    for p in products:
        print(f"  - [{p['type']}] {p['name']}  ({p['release_date']})")
    for p in products:
        print_product_record(build_record(p))
        print_notes(p)
    no_date = [p for p in products if not p["release_date"]]
    no_img = [p for p in products if not p["image"]]
    no_contents = [p for p in products if not p["contents"]]
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for n, why in skipped:
        print(f"  - {n}: {why}")
    print(f"\n{len(no_date)} product(s) with no release date:")
    for p in no_date:
        print(f"  - {p['name']}  {p['source_url']}")
    print(f"\n{len(no_img)} product(s) with no image:")
    for p in no_img:
        print(f"  - {p['name']}  {p['source_url']}")
    print(f"\n{len(no_contents)} product(s) with no contents:")
    for p in no_contents:
        print(f"  - {p['name']}  {p['source_url']}")


def discover(term):
    products, _ = get_all_products()
    matches = [p for p in products if term.lower() in p["name"].lower()]
    if not matches:
        print(f"No product found matching '{term}'")
        return
    for p in matches:
        print_product_record(build_record(p))
        print_notes(p)
        if _DEBUG:
            body = p["_body"]
            if p.get("image"):
                print("\n[DEBUG] text found next to this product's photo:")
                print("  " + clean(_text_near_image(body, p["image"]))[:1500])
            print("\n[DEBUG] images on this page (src | alt | type):")
            for s, a in page_images(body):
                print(f"  {s} | {a} | {classify(a + ' ' + s)}")
            print("[DEBUG] headings:")
            for t, frag in heading_blocks(body):
                print(f"  [{classify(t)}] {t[:90]}  ->  {clean(frag)[:200]}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() != "all":
        discover(sys.argv[1])
    elif len(sys.argv) >= 2:
        discover_all()
    else:
        term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        discover(term) if term else discover_all()