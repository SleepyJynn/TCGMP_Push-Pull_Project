"""
Discovery script for Riftbound sealed product data (Riot merch store).

Confirmed working sources (29 Sep 2026):
    Catalog: https://merch.riotgames.com/productPreviews/en-us.json.gz
    Product page: https://merch.riotgames.com/en-us/product/<slug>/

Scope settled (29 Sep 2026): pull EVERY official physical Riftbound product
from the catalog (boosters, decks, sleeves, playmats, everything) in one
pass, the same "pull ALL" scope as One Piece — no more single-search-term-
only CLI. Digital-only items (Portal Icon, Emote) are excluded per explicit
instruction: they're League client cosmetics, not physical TCG product.

Key differences from the One Piece source, confirmed via live diagnostic
dumps the user ran (see inspect_riftbound_catalog.py / inspect_riftbound_dates.py)
and real product-page View Source the user pasted:

  - The catalog is a dict keyed by SKU, so there is no pagination and no
    duplicate risk the way there was for One Piece's paginated listing.
  - There is NO structured category field (no equivalent of One Piece's
    data-cat). Category is inferred from title keywords instead — a weaker
    signal, but confirmed (29 Sep 2026) against a live run: all 28 real
    physical items classify correctly, none fall through to "Other".
  - There is NO genuine release-date field anywhere, on the catalog or on
    the product page itself: `drops_at` (catalog) / `dropAt` (page) is
    null/undefined for every item with no exception. The old version's
    workaround of scraping Wikipedia for a date near the search term was
    fragile, didn't scale to "pull everything", and its accuracy was never
    verified — it's been removed. Instead we use the catalog's `_createdAt`
    field directly (confirmed to match the same value exposed on the
    product page itself), labelled honestly as a "listed_date" rather than
    a claimed release date, since that's what it actually is.
    UPDATE 12 Oct 2026: real release dates are now looked up per set and
    kept in _RELEASE_DATES (with a source link per row); the listing date
    is only shown for products that table doesn't cover yet.
  - There is NO structured Contents/spec table like One Piece's
    div.detailColStatus dt/dd pairs — only free-text marketing prose, e.g.
    "Each booster display contains 24 booster packs." or "Ready to Play -
    2 full 56-card preconstructed decks... Includes 2 full-size paper
    playmats and 2 booster packs for customization." This varies in
    wording per product type with no consistent structure, so rather than
    force it into a fabricated structured contents list (which would be
    guesswork for anything that isn't a plain booster display), we store
    the raw description text as-is. This was an explicit decision, not a
    default.
  - That description text is NOT in the page's visible DOM at all (the
    page is entirely client-rendered, Next.js/React Server Components) --
    it's a lazy-loaded reference (e.g. "copy": "$18") into the RSC data
    stream embedded in self.__next_f.push(...) script tags.
    extract_description() below resolves that reference properly. An
    earlier version instead grepped the raw page text for runs of <p>
    tags, which worked by coincidence for boosters/decks but silently
    corrupted the output for sleeves/playmats (leaked raw trailing JSON
    into the Contents field) — caught via a live run and fixed by
    verifying byte-for-byte against the real saved HTML before shipping.

Usage:
    python src/discovery_riftbound.py all --out=riftbound_output.txt   (every product, into a UTF-8 file)
    python src/discovery_riftbound.py "Poppy" --debug   (one product, showing every description on its page)
    python src/discovery_riftbound.py                  (prompts; leave blank to pull all)
    python src/discovery_riftbound.py "Vendetta"        (search within all products)
"""

import sys

# Same --out=<file> option as the Digimon and FaB scripts: writes the output
# as UTF-8 to a file (and still prints it), so long runs don't get cut off.
#   python src/discovery_riftbound.py all --out=riftbound_output.txt
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

# --debug: with a single-product search, also prints every description found
# on the page with its score, and the cleaned text the contents parser saw.
_DEBUG = "--debug" in sys.argv
if _DEBUG:
    sys.argv.remove("--debug")

import gzip
import html as html_lib  # aliased -- "html" is already used as the page-source parameter name throughout this file
import json
import re
import sys
import requests
from common import make_product_record, print_product_record

CATALOG_URL = "https://merch.riotgames.com/productPreviews/en-us.json.gz"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
})

# Title-keyword -> category. Checked in order, first match wins, so put more
# specific phrases before more generic ones (e.g. "Showdown Deck" before
# "Deck"). NEEDS VERIFICATION against the real 28-item title list before
# treating this as final — flag back any title that falls through to "Other".
_CATEGORY_KEYWORDS = [
    ("Booster Display", ["booster display"]),
    ("Booster Pack", ["booster pack"]),
    ("Showdown Deck", ["showdown deck", "showdown"]),
    ("Champion Deck", ["champion deck"]),
    ("Starter Deck", ["starter deck"]),
    ("Signature Edition", ["signature edition"]),
    ("Box Set", ["box set"]),
    ("Bundle", ["bundle"]),
    ("Vault", ["vault"]),
    ("Playmat", ["playmat"]),
    ("Sleeves", ["sleeve"]),
    ("Proving Grounds", ["proving grounds"]),
    ("Deck", ["deck"]),
]


def classify_category(title):
    title_lower = (title or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(kw in title_lower for kw in keywords):
            return category
    return "Other"


def fetch_catalog():
    resp = session.get(CATALOG_URL, timeout=20)
    resp.raise_for_status()
    return json.loads(gzip.decompress(resp.content))


def fetch_product_page_html(slug):
    url = f"https://merch.riotgames.com/en-us/product/{slug}/"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


_PUSH_RE = re.compile(r'self\.__next_f\.push\((\[1,"(?:[^"\\]|\\.)*"\])\)')


def _decode_next_f_chunks(html):
    """Each self.__next_f.push([1, "..."]) call's argument is a valid JSON
    array literal: [1, "<JSON-escaped string>"]. json.loads on the whole
    array gives the real decoded text (quotes, unicode, HTML tags and all)
    -- this is what a first version of this function got wrong by hand-
    replacing \\u003c/\\u003e and quote escapes itself, which drifted past
    the real content boundary for some product types (see extract_description
    docstring)."""
    chunks = []
    for m in _PUSH_RE.finditer(html):
        try:
            _, text = json.loads(m.group(1))
            chunks.append(text)
        except (json.JSONDecodeError, ValueError):
            continue
    return chunks


def _resolve_next_f_ref(full, full_bytes, ref_id):
    """Resolve a React Flight lazy reference like "$18" against the
    concatenated, decoded push stream. Large string values are framed as
    rows of the form '<id>:T<hexlen>,<text>' where hexlen is the payload's
    length in UTF-8 BYTES (not characters, and not newline-terminated) --
    slicing by character count instead overshoots whenever the text has a
    multi-byte character (an em dash, a curly apostrophe) and leaks the
    start of the next row into the result. Confirmed against real
    Booster Display and Showdown Deck page source (29 Sep 2026): fixing
    this byte-vs-character mismatch is what stopped a trailing
    '7:["$",...' JSON fragment from being appended to the description."""
    m = re.search(rf'(?:^|\n){re.escape(ref_id)}:T([0-9a-fA-F]+),', full)
    if m:
        length = int(m.group(1), 16)
        byte_start = len(full[:m.end()].encode("utf-8"))
        return full_bytes[byte_start:byte_start + length].decode("utf-8", errors="replace")
    # Fallback for a non-text ("id:{...}" / "id:[...]") row: no declared
    # length, so cut at the next row boundary instead.
    m2 = re.search(rf'(?:^|\n){re.escape(ref_id)}:(.*)', full, re.DOTALL)
    if not m2:
        return None
    rest = m2.group(1)
    end = re.search(r'\n[0-9a-fA-F]+:', rest)
    return rest[:end.start()] if end else rest


def extract_description_candidates(html):
    """All "copy" texts on a Riftbound product page, resolved and cleaned.

    Confirmed (29 Sep 2026) against real View Source for a Booster Display
    and a Showdown Deck: the page is entirely client-rendered (Next.js), so
    the description isn't in the visible DOM. It lives in the product's
    "info" array as {"tabLabel": "...description", "copy": "$18"} -- "copy"
    is a React Flight lazy reference resolved against the decoded RSC stream.
    """
    full = "".join(_decode_next_f_chunks(html))
    full_bytes = full.encode("utf-8")

    candidates = []
    for cm in re.finditer(r'"copy"\s*:\s*"(\$[0-9a-zA-Z]+|(?:[^"\\]|\\.)*)"', full):
        val = cm.group(1)
        if val.startswith("$"):
            text = _resolve_next_f_ref(full, full_bytes, val[1:])
            if text is None:
                continue
        else:
            try:
                text = json.loads('"' + val + '"')
            except (json.JSONDecodeError, ValueError):
                text = val
        # Line breaks are kept (12 Oct 2026): the Worlds Bundle lists its
        # items one per line with no punctuation, so collapsing them ran
        # "...host cities Foiled playmat and matching deckbox Card sleeves"
        # together. Only the "Features:" list uses them; everything else
        # works on a flattened copy (see extract_contents).
        text = re.sub(r"<br\s*/?>|</(?:p|li|div|h\d)>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_lib.unescape(text)  # &amp; / &#x27; / &nbsp; etc.
        text = re.sub(r"[^\S\n]+", " ", text)
        text = re.sub(r" *\n[\n ]*", "\n", text).strip()
        if text and text not in candidates:
            candidates.append(text)
    return candidates


# ---------------------------------------------------------------------------
# Picking the RIGHT description -- FIXED (12 Oct 2026, full live run)
#
# The old rule "the longest copy on the page is the product's own" was wrong
# for 9 of 28 products: every sleeve and playmat page came back with the
# Unleashed Booster Display text ("Unleashed captures the heart of League of
# Legends..."), and the Arcane Box Set came back with Proving Grounds' text.
# Those pages carry a companion-product tile whose copy is longer than the
# product's own. Each candidate is now scored on how many of the product's
# own name words it mentions ("poppy", "hammer", "arcane"...) plus whether it
# mentions the product's type (sleeve / playmat / vault...); length only
# breaks ties. If no candidate mentions the product at all, the longest is
# still used but the product is listed for a manual check at the end of a
# full run (see discover_all).
# ---------------------------------------------------------------------------
_GENERIC_TITLE_WORDS = {
    "riftbound", "league", "legends", "legendsᵀᴹ", "tcg", "of", "the", "and", "vs", "deck", "decks",
    "champion", "booster", "display", "art", "sleeves", "playmat", "edition", "bundle", "set", "box",
    "showdown", "language", "chinese", "x",
}
_TYPE_WORDS = {
    "Sleeves": ["sleeve"], "Playmat": ["playmat"], "Vault": ["vault"], "Box Set": ["box set"],
    "Proving Grounds": ["proving grounds"], "Showdown Deck": ["showdown"], "Champion Deck": ["champion deck"],
    "Booster Display": ["booster display"], "Bundle": ["bundle"], "Signature Edition": ["signature"],
}


def _title_words(title):
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if len(w) >= 3 and w not in _GENERIC_TITLE_WORDS}


# Every catalog title, filled in by get_all_riftbound_products(). Used to spot
# a description that names a DIFFERENT product (see score_description).
_ALL_TITLES = []
_TITLE_PREFIX_RE = re.compile(r"^.*?league of legends\S*\s*(?:tcg)?\s*[-:–]?\s*", re.IGNORECASE)


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _core_name(title):
    """'Riftbound: League of Legendsᵀᴹ Arcane Box Set' -> 'arcane box set'."""
    return _norm(_TITLE_PREFIX_RE.sub("", title or ""))


def _names_other_product(text, title):
    """True if the text names another catalog product by its full name
    (e.g. 'Arcane Box Set' on the Jinx deck page) that this product's own
    title doesn't contain."""
    low, own = f" {_norm(text)} ", f" {_norm(title)} "
    for other in _ALL_TITLES:
        core = _core_name(other)
        if other == title or len(core.split()) < 2 or f" {core} " in own:
            continue
        if f" {core} " in low:
            return True
    return False


def score_description(text, title, category):
    """Returns (score, is_about_this_product).

    "About this product" needs at least half of the product's own name words
    -- one shared word isn't enough, because the Unleashed booster copy
    mentions "Poppy" and "LeBlanc" in passing ("champions like Rengar, Vi,
    Poppy, and LeBlanc") and would otherwise pass as the Poppy playmat's own
    description. Sleeves and playmats must also mention "sleeve"/"playmat".

    FIXED 12 Oct 2026 (debug runs on Jinx / Poppy sleeves / Arcane):
      - The Jinx deck page also carries the Arcane Box Set's text, which
        mentions "Jinx" and "Champion deck" ("Note this product is not a
        Champion deck ... foil treatments of Jinx, Vi ...") and outscored
        Jinx's own text. A text that names another catalog product by its
        full name ("Arcane Box Set") is now treated as that product's.
      - The Poppy sleeves page carries the matching Poppy playmat's text;
        both name "Poppy, Keeper of the Hammer", and the playmat one ends
        "Pair Up Sleeves with matching Champions..." so it passed the sleeve
        check and won on length. A text that mentions the OTHER accessory
        type more often than this one (3x playmat vs 1x sleeve) is now
        treated as the other product's."""
    low = text.lower()
    own = _title_words(title)
    hits = sum(1 for w in own if re.search(r"\b" + re.escape(w) + r"\b", low))
    type_hit = any(k in low for k in _TYPE_WORDS.get(category, []))
    about = hits >= max(1, -(-len(own) // 2))
    if category in ("Sleeves", "Playmat"):
        mine, theirs = ("sleeve", "playmat") if category == "Sleeves" else ("playmat", "sleeve")
        if not type_hit or low.count(theirs) > low.count(mine):
            about = False
    if about and _names_other_product(text, title):
        about = False
    return hits * 10 + (5 if type_hit else 0), about


def choose_description(candidates, title, category):
    """Returns (text, mentions_product, scored_list)."""
    if not candidates:
        return None, False, []
    scored = [(score_description(c, title, category), len(c), c) for c in candidates]
    # Descriptions that are about this product first, then score, then length.
    scored.sort(key=lambda x: (x[0][1], x[0][0], x[1]), reverse=True)
    best = scored[0]
    return best[2], best[0][1], [(sc, ln, c) for sc, ln, c in scored]


# ---------------------------------------------------------------------------
# Contents parsing (NEW, 12 Oct 2026)
#
# The description is marketing prose, but the useful facts use the same
# phrasing across every Riftbound product seen in the 28-item live run:
#   "These 14-card booster packs pull from a pool of over 160 cards, with
#    more than 50 Showcase cards"
#   "premium packs filled with 7 Commons, 3 Uncommons, 2 foil Rares or
#    better, 1 foil of any rarity, and 1 token slot."
#   "Each booster display contains 24 booster packs."
#   "2 full 56-card preconstructed decks" / "A full 56-card preconstructed deck"
#   "Includes 2 full-size paper playmats and 2 booster packs ..."
#   "inside you'll find 6 Booster Packs ..., 36 basic runes ..." (Vault)
#   "Features: 1x ... 1x ... 3x ..." (Lunar Revel Bundle)
# Only those facts are kept, as short labelled lines. The "PURCHASING
# DISCLAIMER(S)" block every page starts with is removed first. If none of
# the patterns match, the first two sentences of the description are kept
# (labelled "Description:") so nothing is silently lost.
# ---------------------------------------------------------------------------
_DISCLAIMER_START_RE = re.compile(r"PURCHASING DISCLAIMER\(S\)", re.IGNORECASE)
_DISCLAIMER_END_RE = re.compile(
    r"(?:in which the purchase is made\.|not refundable\.?|cannot be returned\.|"
    r"A signature may be required upon delivery\.)"
)


def strip_disclaimer(text):
    m = _DISCLAIMER_START_RE.search(text)
    if not m:
        return text.strip(), None
    window = text[m.start():m.start() + 700]
    ends = list(_DISCLAIMER_END_RE.finditer(window))
    if not ends:
        return text.strip(), None
    disclaimer = window[:ends[-1].end()]
    rest = (text[:m.start()] + text[m.start() + ends[-1].end():]).strip()
    return rest, disclaimer


_WORD_NUM = {"a": "1", "an": "1", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
             "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10", "twelve": "12"}


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text) if s.strip()]


# Tidy-up (12 Oct 2026): the "Includes" sentences repeat themselves -- the
# Radiance Showdown Deck page says "two booster packs" in one sentence and
# "2 Riftbound: Radiance boosters" / "2 Radiance booster packs for
# customization" in two more. Each sentence is split into single items, the
# "for customization" / "to power your decks" tails are dropped, and only the
# FIRST item of each kind (booster, playmat, deck box, rulebook...) is kept.
_NUM_WORDS_RE = r"(?:\d+|two|three|four|five|six|a|an|one)"
_ITEM_SPLIT_RE = re.compile(
    rf",\s+(?={_NUM_WORDS_RE}\b)"              # ", 36 basic runes"
    rf"|,?\s+and\s+(?={_NUM_WORDS_RE}\b)"      # " and 2 durable deck boxes"
    r"|,\s+and\s+(?=(?-i:[a-z]))",            # ", and deck-building tips" (not ", and Garen")
    re.IGNORECASE)
_PURPOSE_TAIL_RE = re.compile(
    r"\s+(?:designed\s+)?(?:for (?:customization|keeping|balanced)|to help|to power|to keep|making it)\b.*$",
    re.IGNORECASE)
_ITEM_KINDS = [
    ("deck box", r"\bdeck ?box(?:es)?\b"), ("playmat", r"\bplaymats?\b"), ("booster", r"\bboosters?\b"),
    ("rulebook", r"\brulebooks?\b"), ("sleeves", r"\bsleeves?\b"), ("runes", r"\brunes?\b"),
    ("tokens", r"\btokens?\b"), ("dividers", r"\bdividers?\b"), ("deck", r"\bdecks?\b(?!-)"),
]


def _split_items(item):
    out = []
    for part in _ITEM_SPLIT_RE.split(item):
        part = _PURPOSE_TAIL_RE.sub("", part).strip(" ,;.")
        if not part:
            continue
        first, _, rest = part.partition(" ")
        if first.lower() in _WORD_NUM and rest:
            part = f"{_WORD_NUM[first.lower()]} {rest}"
        out.append(part)
    return out


def _dedupe_items(items, have_decks=False):
    seen, out = ({"deck"} if have_decks else set()), []
    for it in items:
        kind = next((k for k, rx in _ITEM_KINDS if re.search(rx, it, re.IGNORECASE)), it.lower())
        if kind in seen:
            continue
        seen.add(kind)
        out.append(it)
    return out


def extract_contents(description):
    if not description:
        return []
    lined, disclaimer = strip_disclaimer(description)
    text = re.sub(r"\s+", " ", lined)
    disclaimer = re.sub(r"\s+", " ", disclaimer) if disclaimer else disclaimer
    lines = []

    # --- booster sets ---
    pack = re.search(r"(\d+)-card booster packs?", text)
    pool = re.search(r"pull from (?:a pool of )?((?:over|nearly|more than|almost) )?(\d+) cards", text)
    showcase = re.search(r"((?:more than|over) )?(\d+)\+? (Showcase cards|alt-arts|alts)\b", text)
    if pool:
        extra = f" ({(showcase.group(1) or '')}{showcase.group(2)}{'+' if '+' in showcase.group(0) else ''} {showcase.group(3)})" if showcase else ""
        lines.append(f"Cards in set: {(pool.group(1) or '')}{pool.group(2)}{extra}")
    slots = re.search(r"packs filled with (.+?)\.(?:\s|$)", text)
    if pack or slots:
        size = f"{pack.group(1)} cards" if pack else ""
        detail = slots.group(1).replace(", and ", ", ") if slots else ""
        lines.append("Pack configuration: " + " — ".join(x for x in (size, detail) if x))
    display = re.search(r"Each booster display contains (\d+) booster packs", text)
    if display:
        lines.append(f"Display: {display.group(1)} booster packs")

    # --- preconstructed decks ---
    deck = re.search(r"\b(\d+|[Tt]wo|[Aa]n?|[Oo]ne) (?:full )?(\d+)-card preconstructed decks?", text)
    if deck:
        n = _WORD_NUM.get(deck.group(1).lower(), deck.group(1))
        lines.append(f"Decks: {n} × {deck.group(2)}-card preconstructed deck")

    # --- Champion Legend sets (Arcane Box Set, confirmed 12 Oct 2026):
    # "only contains six individual Champion Legend cards ... foil treatments
    # of Jinx, Vi, Heimerdinger, Caitlyn, Viktor and Warwick"
    legends = re.search(r"contains (\w+) (?:individual )?Champion Legend cards", text, re.IGNORECASE)
    if legends:
        n = _WORD_NUM.get(legends.group(1).lower(), legends.group(1))
        who = re.search(r"(foil)?(?: treatments)? of ((?:[A-Z][\w']+(?:, | and ))+[A-Z][\w']+)", text)
        names = ""
        if who:
            names = f" ({'foil: ' if who.group(1) else ''}{re.sub(r' and ', ', ', who.group(2))})"
        lines.append(f"Champion Legend cards: {n}{names}")

    # --- "what's in the box" sentences ---
    included = []
    for sent in _sentences(text):
        m = re.search(r"(?:\bIncludes:?|\bComes with|Jump into play with|inside you(?:’|')ll find|this bundle features)\s+(.+)",
                      sent, re.IGNORECASE)
        if not m:
            continue
        item = m.group(1).strip().rstrip(".!")
        # "3 rares with a clear path to competitive upgrades—perfect for ..."
        item = re.split(r"\s*(?:—| - )\s*", item)[0].strip()
        if len(item) > 220:
            continue
        included.extend(_split_items(item))
    feats = re.search(r"Features:\s*(.+?)(?=\s+Approximate Dimensions|\s+Please note|$)", lined, re.DOTALL)
    if feats:
        included.extend(x.strip() for x in re.split(r"\n|\s(?=\d+x\s)", feats.group(1)) if x.strip())
    included = _dedupe_items(included, have_decks=bool(deck))
    if included:
        lines.append("Includes: " + "; ".join(included))

    # Chaser odds live in the disclaimer on the Lunar Revel bundle.
    chaser = re.search(r"((?:[A-Z]\w+ )+\w+) chaser ratio: (\d+ in \d+)", disclaimer or "")
    if chaser:
        lines.append(f"Chaser: {chaser.group(1)} (1 per {chaser.group(2).split(' in ')[1]} bundles)"
                     if chaser.group(2).startswith("1 in ") else f"Chaser: {chaser.group(1)} {chaser.group(2)}")
    serial = re.search(r"with ([\d,]+) versions produced", text)
    if serial:
        lines.append(f"Print run: {serial.group(1)}")

    # Accessories (not seen live yet -- the 8 sleeve/playmat pages only carried
    # the booster display's text in the 12 Oct run): sleeve counts and mat size.
    sleeves = re.search(r"\b(\d+)\s*(?:ct\b|count\b|(?:art |card )?sleeves\b)", text, re.IGNORECASE)
    if sleeves and not any(l.startswith("Includes:") and "sleeve" in l.lower() for l in lines):
        lines.append(f"Sleeves: {sleeves.group(1)}")
    size = re.search(r"(\d+(?:\.\d+)?\s*(?:x|×)\s*\d+(?:\.\d+)?\s*(?:inch(?:es)?|in\b\.?|cm|mm|\"))", text, re.IGNORECASE)
    if size:
        lines.append(f"Size: {size.group(1)}")

    if not lines:
        first = next((x for x in _sentences(text) if len(x) <= 250), None)
        if first:
            lines.append("Description: " + first)
    return lines


# ---------------------------------------------------------------------------
# Real release dates (NEW, 12 Oct 2026)
#
# The store has no release-date field and its _createdAt listing date is
# wrong for most items (e.g. Origins listed 4 Aug 2025 but released 31 Oct
# 2025; the Arcane Box Set shows 2 Sep 2026 but went on pre-order 2 Sep
# 2025). Real dates were looked up per set and are matched on title words.
# Retail sets use the English street date from UVS Games' "Dates and Details"
# sell sheets / Riot press releases. Riot-store exclusives (bundles, Arcane,
# T1) never had a street date, so the date they went ON SALE is used and
# the note says when they shipped. First match wins -- keep specific rules
# (e.g. "Proving Grounds") above set-wide ones.
# ---------------------------------------------------------------------------
_RELEASE_DATES = [
    # (title words that must ALL appear, date, note, source)
    (["proving grounds"], "2025-10-31",
     "Original Proving Grounds (Annie/Master Yi/Lux/Garen) released with Origins. The store's $39.99 price is the reprint "
     "(MSRP raised from $29.99); no reprint date has been announced.",
     "https://riftboundguide.com/2026/02/28/riftbound-proving-grounds-reprint/"),
    (["arcane box set"], "2025-09-02",
     "Riot-store pre-order opened 2 Sep 2025; production was delayed and shipping started mid-Feb 2026 (no exact day published).",
     "https://playriftbound.com/en-us/news/announcements/how-to-preorder-the-riftbound-lol-arcane-box-set/"),
    (["worlds bundle 2025"], "2025-10-28",
     "On sale on the Riot store 28 Oct 2025, 9AM PT; orders expected to ship mid-December 2025.",
     "https://playriftbound.com/en-us/news/announcements/how-to-buy-riftbound-worlds-bundle-2025-and-origins-restock-news/"),
    (["lunar revel"], "2026-02-17",
     "On sale on Riot's store 17 Feb 2026.",
     "https://www.sheepesports.com/articles/riftbound-all-collector-bundles-and-promotional-cards/en"),
    (["t1", "signature edition"], "2026-08-20",
     "Sold by draw on the Riot store: entries 14-17 Aug 2026, purchase emails from 20 Aug 2026, shipping from 2 Sep 2026.",
     "https://riftbound.gg/riftbound-t1-bundle-draw-schedule/"),
    # The 4 Unleashed playmats + 4 art sleeves don't say "Unleashed" in their
    # titles; their page text does ("official Riftbound: Unleashed sleeves"),
    # and the Asmodee distributor listing gives "May 8 2026".
    *[([champ], "2026-05-08", "Unleashed accessory (playmat / art sleeves) -- released with the set.",
       "https://uvsgames.com/wp-content/uploads/2026/01/Unleashed-Dates-and-Details.pdf")
      for champ in ("poppy, keeper of the hammer", "leblanc, deceiver", "master yi, wuju master", "vi, piltover enforcer")],
    (["origins"], "2025-10-31", "English release of Origins (Set 1).",
     "https://pressreleases.triplepointpr.com/2025/10/31/riftbound-league-of-legends-trading-card-game-first-set-origins-now-available/"),
    (["spiritforged"], "2026-02-13", "English release of Spiritforged (Set 2).",
     "https://uvsgames.com/wp-content/uploads/2025/11/Spiritforged-Dates-and-Details.pdf"),
    (["unleashed"], "2026-05-08", "English release of Unleashed (Set 3); playmats and art sleeves release with the set.",
     "https://uvsgames.com/wp-content/uploads/2026/01/Unleashed-Dates-and-Details.pdf"),
    (["vendetta"], "2026-07-31", "English release of Vendetta (Set 4).",
     "https://articles.starcitygames.com/news/riftbound-2026-set-release-schedule/"),
    (["radiance"], "2026-10-23", "English release of Radiance (Set 5) -- UPCOMING, not out yet.",
     "https://uvsgames.com/wp-content/uploads/2026/07/Radiance-Dates-and-Details.pdf"),
]


def lookup_release_date(title):
    """Returns (date, note, source) or (None, None, None) for a product not in
    the table -- a NEW product the table doesn't know yet, which needs adding."""
    low = (title or "").lower()
    for words, date, note, source in _RELEASE_DATES:
        if all(w in low for w in words):
            return date, note, source
    return None, None, None


def get_all_riftbound_products():
    """Pull every physical Riftbound product from the catalog. Excludes the
    two digital-only items (Portal Icon, Emote) per explicit instruction --
    product_type == "digital" is the confirmed signal for those, checked
    against a full field dump of all 30 Riftbound-branded catalog entries.
    No dedup needed: the catalog is a dict keyed by SKU, so it's already
    unique."""
    data = fetch_catalog()
    riftbound_items = {
        k: v for k, v in data.items()
        if (v.get("brand") or {}).get("slug") == "riftbound"
    }
    physical_items = {
        k: v for k, v in riftbound_items.items()
        if v.get("product_type") != "digital"
    }

    _ALL_TITLES[:] = [v.get("title") or "" for v in physical_items.values()]
    products = []
    for sku, item in physical_items.items():
        slug = item.get("slug")
        products.append({
            "sku": sku,
            "title": item.get("title"),
            "category": classify_category(item.get("title")),
            "price": item.get("price"),
            "listed_date": item.get("_createdAt"),  # site listing date, NOT a confirmed retail release date -- see module docstring
            "release_date": lookup_release_date(item.get("title"))[0],
            "image_url": (item.get("main_image") or {}).get("src"),
            "slug": slug,
            "source_url": f"https://merch.riotgames.com/en-us/product/{slug}/" if slug else None,
        })
    return products


def search_products(products, search_term):
    search_norm = search_term.lower()
    return [p for p in products if search_norm in (p["title"] or "").lower()]


def resolve_match(matches, search_term):
    if len(matches) == 1:
        return matches[0]

    search_norm = search_term.lower()
    exact = [m for m in matches if (m["title"] or "").lower() == search_norm]
    if len(exact) == 1:
        return exact[0]

    print(f"\n'{search_term}' matched {len(matches)} products — which one did you mean?")
    for i, m in enumerate(matches, start=1):
        print(f"  {i}. [{m['category']}] {m['title']}")
    choice = input(f"Enter number (1-{len(matches)}), or blank to cancel: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(matches)):
        print("Cancelled.")
        return None
    return matches[int(choice) - 1]


def build_record(product):
    candidates = []
    try:
        if product.get("slug"):
            page_html = fetch_product_page_html(product["slug"])
            candidates = extract_description_candidates(page_html)
    except requests.exceptions.RequestException as e:
        print(f"  (couldn't fetch page for {product.get('slug')}: {e})")

    description, mentions, scored = choose_description(candidates, product["title"], product["category"])
    # A description that never mentions the product's own name is almost
    # certainly another product's text (a companion tile) -- parsing it would
    # give sleeves a booster's card counts. Left empty and listed instead.
    contents = extract_contents(description) if (mentions or not _title_words(product["title"])) else []
    product["_desc_mentions_product"] = mentions
    product["_desc_scored"] = scored
    product["_description"] = description
    product["_contents"] = contents

    return make_product_record(
        game="Riftbound",
        title=product["title"],
        sku=product["sku"],
        # Real release date from _RELEASE_DATES; None if the product isn't in
        # that table yet (it's then listed at the end of a full run).
        release_date=product.get("release_date"),
        image_url=product.get("image_url"),
        contents=contents,
        pack_configuration=[],
        price=product.get("price"),
        source_url=product.get("source_url"),
    )


def print_release_note(product):
    _, note, source = lookup_release_date(product["title"])
    if note:
        print(f"Release note: {note}")
        print(f"Release source: {source}")
    else:
        print(f"Release note: NOT IN DATE TABLE -- store listing date is {product.get('listed_date')}; add it to _RELEASE_DATES")


def discover_all():
    products = get_all_riftbound_products()
    print(f"[DEBUG] Found {len(products)} physical Riftbound products (digital items excluded):")
    for p in products:
        print(f"  - [{p['category']}] '{p['title']}'  (SKU {p['sku']}, listed {p['listed_date']})")

    records = []
    for p in products:
        record = build_record(p)
        print_product_record(record)
        print_release_note(p)
        records.append(record)

    unsure = [p for p in products if p.get("_description") and not p["_desc_mentions_product"]
              and _title_words(p["title"])]
    print(f"\n{len(unsure)} product(s) left with NO contents because every description on the page is about "
          f"a different product (check with --debug):")
    for p in unsure:
        print(f"  - {p['title']}  {p['source_url']}")
    fallback = [p for p in products if any(c.startswith("Description:") for c in p.get("_contents", []))]
    print(f"\n{len(fallback)} product(s) where no card count / contents phrase was found (first sentences kept instead):")
    for p in fallback:
        print(f"  - {p['title']}")
    no_desc = [p for p in products if not p.get("_description")]
    print(f"\n{len(no_desc)} product(s) with no description found on the page:")
    for p in no_desc:
        print(f"  - {p['title']}  {p['source_url']}")
    no_date = [p for p in products if not p.get("release_date")]
    print(f"\n{len(no_date)} product(s) with no real release date (not in _RELEASE_DATES yet -- new products):")
    for p in no_date:
        print(f"  - {p['title']}  (store listing date {p['listed_date']})")
    return records


def discover(search_term):
    products = get_all_riftbound_products()
    matches = search_products(products, search_term)

    if not matches:
        print(f"No product found matching '{search_term}'")
        return

    match = resolve_match(matches, search_term)
    if match is None:
        return

    record = build_record(match)
    print_product_record(record)
    print_release_note(match)
    if _DEBUG:
        print("\n[DEBUG] description candidates on the page (about-this-product, score, length) -- first wins:")
        for (score, about), length, text in match["_desc_scored"]:
            print(f"  about={str(about):<5} score {score:>3}  len {length}:")
            print(f"      {strip_disclaimer(text)[0]}")
        print("\n[DEBUG] chosen description, disclaimer removed:")
        print(strip_disclaimer(match["_description"] or "")[0])
    return record


if __name__ == "__main__":
    # A CLI arg still works (python discovery_riftbound.py "Vendetta") for
    # scripting, but matching the One Piece script's interactive style:
    # normally you just run it with no args and it prompts instead of
    # needing a quoted search term on the command line. Leaving the prompt
    # blank pulls every product, same as running with no args did before.
    if len(sys.argv) >= 2:
        if sys.argv[1].strip().lower() == "all":
            discover_all()
        else:
            discover(sys.argv[1])
    else:
        search_term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        if search_term:
            discover(search_term)
        else:
            discover_all()