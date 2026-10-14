"""
Discovery script for Digimon Card Game (Bandai) sealed product data.

Confirmed working source (2 Oct 2026), direct from Bandai's own official
sites -- no third-party API, no WordPress/Next.js data layer, just plain
server-rendered PHP pages:
  - EN: https://world.digimoncard.com
  - JP: https://digimoncard.com

Key facts confirmed via live diagnostic dumps the user ran:

  - Each site's single /products/ hub page already links to EVERY product
    (135 unique /products/... links on EN, 159 on JP) -- no pagination, no
    separate archive/backnumber page needed. get_all_products() fetches
    that one page and follows every link.
  - Products fall into 3 URL categories, all under /products/<category>/:
      deck   -- Starter Decks / Advanced Decks (ST-xx)
      pack   -- Booster Packs: standalone (BT-xx, via "verN" slugs),
                Extra/Theme Booster (EX-xx), Advanced Booster (AD-01),
                Resurgence Booster (RB-01)
      goods  -- everything else: sleeves, playmats, binders, gift/adventure
                boxes, premium sets, and Premium Bandai exclusives (PB-xx,
                preorder-only but still an official first-party product, so
                these are NOT excluded as junk)
    Some URLs end in ".php" (older products), others in "/" (newer, e.g.
    st-17/, ex-06/) -- both are real pages, just handled as-is.
  - No real junk/test-post problem like FAB's WP API had -- every linked
    page is a real, official, hand-maintained product page (this is
    Bandai's own curated site, not an open CMS with abandoned drafts).
  - Every product page ends its own content with a shared sidebar/footer
    product-list widget listing dozens of OTHER products. All field
    extraction must run only against the page's own content BEFORE that
    widget -- see the FOOTER CUT comment further down for how that is
    done (BUG #3, fixed 7 Oct 2026).
  - BUG #2 FOUND AND FIXED across two live runs: title/code extraction
    reads the page's own <title> tag directly (raw HTML, confirmed via a
    live dump to hold EXACTLY "DIGIMON CARD GAME <name>[ [<code>]] −
    PRODUCTS｜Digimon Card Game" with no surrounding noise, on every
    template checked) instead of guessing at body-text structure.
  - Also fixed: responses weren't reliably decoded as UTF-8 (requests
    guessed a wrong charset), producing mojibake like "ΓêÆ" / "┬⌐" in
    titles and contents text -- both the hub page and per-product fetches
    now force resp.encoding = "utf-8".
  - Not every product has a bracketed set code -- some plain goods items
    (e.g. "Official Card Sleeves Silver") have no code at all, correctly
    left as None rather than borrowing one from a nearby product.
  - Release date text is inconsistent in format across products -- full
    dates ("January 29th, 2021"), month+year only ("March, 2023"),
    "onwards" suffixes, and sometimes TWO regional dates, or a separate
    "Pre-Release date" field. This script captures the primary "Release
    date" text as-is (not reformatted/guessed into a single clean date),
    and separately captures a pre-release date when present.
  - MSRP is present on most pages ("MSRP : 9.99 USD", "MSRP: 100.00 USD",
    "MSRP 4.99 USD" -- inconsistent spacing/colon, handled loosely).
  - "Contents" (pack configuration / what's in the box) is short free text
    and stored as-is. "Card types" (種類数) and "Rarity" (レアリティ) are
    separate labelled fields on pack/deck pages and are captured on their
    own lines when present.
  - Product image: every page checked has its package shot as an <img>
    whose src ends in img_pkg.png (alt="PACKAGE"), relative to the page.
    The og:image meta is a generic site-wide ogp.jpg on every page, so it
    is NOT used.
  - A product page with no parseable Release date AND no MSRP is flagged
    for manual review rather than silently guessed at or dropped.

Usage:
    python src/discovery_digimon.py
      -> prompts: language (EN/JP), then product name (blank = pull all)
    python src/discovery_digimon.py EN "Gaia Red"
      -> skips prompts, searches within the EN catalog
"""

import sys
# Force UTF-8 output regardless of how this is invoked -- otherwise Windows'
# default cp1252 console/file encoding crashes on Japanese text from the JP
# site.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# `chcp 65001` only fixes what the terminal DISPLAYS -- it does not change
# how PowerShell's `>` redirection re-encodes a subprocess's output when
# writing it to a file. Writing the file ourselves (explicit UTF-8) sidesteps
# that entirely. Pass --out=<path> to use it, e.g.:
#   python src/discovery_digimon.py JP --out=digimon_jp_output.txt
if any(a.startswith("--out=") for a in sys.argv):
    _out_arg = next(a for a in sys.argv if a.startswith("--out="))
    sys.argv.remove(_out_arg)
    _out_path = _out_arg.split("=", 1)[1]
    _out_file = open(_out_path, "w", encoding="utf-8")

    class _Tee:
        def write(self, s):
            try:
                sys.__stdout__.write(s)
            except UnicodeEncodeError:
                sys.__stdout__.write(s.encode("utf-8", "replace").decode("utf-8", "replace"))
            _out_file.write(s)

        def flush(self):
            sys.__stdout__.flush()
            _out_file.flush()

    sys.stdout = _Tee()

# --debug: with a single-product search, also prints the exact flattened page
# text the field regexes ran against, e.g.
#   python src/discovery_digimon.py EN "Wolf of Friendship" --debug --out=st16_debug.txt
_DEBUG = "--debug" in sys.argv
if _DEBUG:
    sys.argv.remove("--debug")

import html
import re
import time
from urllib.parse import unquote, urljoin

import requests
from common import make_product_record, print_product_record

SOURCES = {
    "EN": "https://world.digimoncard.com",
    "JP": "https://digimoncard.com",
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})

_PRODUCT_LINK_RE = re.compile(r'href="(/products/(?:deck|goods|pack)/[^"]+)"')
_CATEGORY_RE = re.compile(r"/products/([^/]+)/")

_CATEGORY_LABELS = {
    "deck": "Deck",
    "pack": "Booster Pack",
    "goods": "Goods",
}


def fetch_all_product_links(base_url):
    """The single /products/ hub page links to every product -- no
    pagination or archive page needed (confirmed live, 2 Oct 2026)."""
    resp = session.get(f"{base_url}/products/", timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    # JP FIXED (7 Oct 2026, JP full run): the JP hub links some products
    # both with and without a trailing slash ("/products/deck/st-25" and
    # "/products/deck/st-25/"), so ST-25 and ST-26 each came out twice.
    # Deduped on the slash-stripped path, keeping the first form seen.
    unique = {}
    for link in _PRODUCT_LINK_RE.findall(resp.text):
        unique.setdefault(link.rstrip("/"), link)
    return sorted(unique.values())


def _clean_text(html):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# FOOTER CUT -- BUG #3 FOUND AND FIXED (7 Oct 2026)
#
# The old approach split the flattened text on the FIRST occurrence of the
# literal string "Booster Packs". But every Booster Pack product page also
# starts its own heading with its category label, e.g.
#   "PRODUCTS  Booster Packs DIGIMON CARD GAME BOOSTER DIMENSIONAL PHASE [BT11]"
# (confirmed live on /products/pack/ver11.php). That heading sits ABOVE the
# release date / MSRP / contents block, so the split cut the page off before
# any field and every Booster Pack came back with release date, price and
# contents all None.
#
# Fix: cut the RAW HTML at the sidebar widget's own header images instead.
# Both sites (EN and JP, confirmed live on ver11.php) open that widget with
# <img src=".../images/products/subnav.png"> followed by
# <img src=".../images/common/article/tit_snavi_products.png">, immediately
# after the product's own disclaimer/questionnaire block and before any
# other product is listed. These are image file names, so no product
# heading or marketing text can collide with them.
#
# Text-level fallbacks (only used if neither marker is ever found):
#   EN: the widget's first heading "Booster Packs" is immediately followed
#       by its first list item "Booster Pack <name>" -- a doubled
#       "Booster Packs Booster Pack" never occurs in a product's own heading.
#   JP: same structure, "拡張パック 拡張パック ...".
# ---------------------------------------------------------------------------
# "subnavi?" -- some older pages may name the file subnavi.png; either way
# tit_snavi_products follows right after it as a second anchor.
_SIDEBAR_MARKER_RE = re.compile(r"/images/products/subnavi?\.|tit_snavi_products", re.IGNORECASE)
_FOOTER_SPLIT_EN_RE = re.compile(r"Booster Packs\s+Booster Pack\b")
_FOOTER_SPLIT_JP_RE = re.compile(r"拡張パック\s*拡張パック")


def _cut_own_html(raw_html):
    """Returns (own_html, cut_found). own_html is everything before the
    sidebar product-list widget."""
    m = _SIDEBAR_MARKER_RE.search(raw_html)
    if m:
        # Back up to the start of the <img ...> tag containing the marker so
        # no half-tag is left dangling at the end.
        tag_start = raw_html.rfind("<", 0, m.start())
        return raw_html[:tag_start if tag_start != -1 else m.start()], True
    return raw_html, False


def _cut_own_text(text):
    """Text-level fallback cut, used only if the raw-HTML marker is missing."""
    for pattern in (_FOOTER_SPLIT_EN_RE, _FOOTER_SPLIT_JP_RE):
        m = pattern.search(text)
        if m:
            return text[:m.start()]
    return text


# ---------------------------------------------------------------------------
# Title / code (from the <title> tag)
#
# JP wraps set codes in full-width brackets "【ST-1】", EN uses "[ST-1]";
# the bracket regex accepts either.
# ---------------------------------------------------------------------------
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TITLE_PREFIX = "DIGIMON CARD GAME"
_TITLE_SUFFIX_RE = re.compile(r"\s*\S*\s*PRODUCTS", re.IGNORECASE)
_ALL_CODES_RE = re.compile(r"[\[【]([A-Za-z0-9\-]+)[\]】]")
_LINK_SLUG_RE = re.compile(r"/([A-Za-z0-9\-]+?)(?:\.php)?/?$")


def _slug_from_link(link):
    """e.g. '/products/deck/st-18/' -> 'ST18' (dashes stripped so it can be
    compared against bracket codes that may or may not include one)."""
    m = _LINK_SLUG_RE.search(link)
    return m.group(1).upper().replace("-", "") if m else None


def extract_title_and_code(raw_html, link=None):
    """Reads the page's own <title> tag directly rather than guessing at
    body-text structure. Not every product has a bracketed code -- left as
    None rather than borrowed from a nearby product.

    A few products are released as a linked PAIR (e.g. ST18+ST19) sharing ONE
    marketing page whose <title> mentions BOTH codes. When more than one
    bracket code is present, the one matching THIS product's own URL slug is
    used instead of just picking the last one."""
    m = _TITLE_TAG_RE.search(raw_html)
    if not m:
        return None, None
    # FIXED (7 Oct 2026, EN full run): some <title> tags carry HTML entities
    # or URL-encoding ("TAMER&#39;S SELECTION BOX", "HACKERS%27 SLUMBER") --
    # both decoded here so the name reads "TAMER'S" / "HACKERS' SLUMBER".
    title = unquote(html.unescape(_clean_text(m.group(1))))
    # JP FIXED (7 Oct 2026, JP full run): some JP titles write codes with a
    # Unicode hyphen ("【ST‐10】", "【ST‐9】" use U+2010), which the ASCII-only
    # code regex never matched, so SKU came back None. Normalised to "-".
    title = re.sub(r"[‐‑‒–—−－]", "-", title)
    # Zero-width spaces (seen in "DC-1グランプリ2024 プレイマット​") removed.
    title = title.replace("​", "")

    suffix_m = _TITLE_SUFFIX_RE.search(title)
    if suffix_m:
        title = title[:suffix_m.start()].strip()

    # Some newer titles repeat the brand mid-title ("BOOSTER PACK DIGIMON CARD
    # GAME TIMELESS BONDS", "EXTRA BOOSTER PACK DIGIMON CARD GAME CHIVALROUS
    # XIII") -- removed wherever it appears, not just as a leading prefix.
    title = re.sub(r"\s*" + _TITLE_PREFIX + r"\s*", " ", title, flags=re.IGNORECASE).strip()

    # JP FIXED (7 Oct 2026, JP full run): JP titles are
    # "<category label> デジモンカードゲーム <name>", e.g. "構築済みデッキ デジモン
    # カードゲーム スタートデッキ ガイアレッド" / "拡張パック デジモンカードゲーム ブース
    # ターパック ディメンショナルフェイズ". The category label (構築済みデッキ / 拡張
    # パック / スタートデッキ / その他 / プレミアムバンダイ) and the brand are dropped,
    # matching how the EN names drop "DIGIMON CARD GAME".
    title = re.sub(
        r"^(?:(?:構築済みデッキ|拡張パック|スタートデッキ|その他|プレミアムバンダイ)\s*)?デジモンカードゲーム\s*",
        "", title,
    ).strip()
    # JP deck names then start with the deck type ("スタートデッキ ガイアレッド");
    # dropped so they match the EN names ("GAIA RED") and the dual-page split
    # names below ("旋風の守護者").
    title = re.sub(r"^(?:スタートデッキ|スターターデッキ|アドバンスデッキ)\s*", "", title).strip()
    # JP FIXED: BT-26's JP <title> is missing its closing bracket
    # ("TIMELESS BONDS【BT-26"), so no code was found. Closed here when a
    # code-shaped bracket is left open at the very end.
    title = re.sub(r"([\[【])([A-Za-z0-9\-]+)\s*$", lambda mm: mm.group(1) + mm.group(2) + ("]" if mm.group(1) == "[" else "】"), title)

    all_codes = _ALL_CODES_RE.findall(title)
    code = None
    if len(all_codes) > 1 and link:
        slug = _slug_from_link(link)
        for c in all_codes:
            if slug and c.upper().replace("-", "") == slug:
                code = c
                break
    if code is None and all_codes:
        code = all_codes[-1]

    if code is not None and len(all_codes) > 1:
        # FIXED (7 Oct 2026, EN full run): shared dual-product pages (ST18+
        # ST19, ST20+ST21) gave BOTH products the identical combined name
        # "Starter Decks GUARDIAN VORTEX [ST18] FABLE WALTZ [ST19]". The title
        # is always "<label> <NAME A> [A] <NAME B> [B]", and the hub page
        # lists them separately as "GUARDIAN VORTEX [ST18]" / "FABLE WALTZ
        # [ST19]", so each product takes the text segment right before its
        # own code, with the leading "Starter Deck(s)" label dropped.
        segments = re.findall(r"(.*?)[\[【]([A-Za-z0-9\-]+)[\]】]", title)
        for seg_text, seg_code in segments:
            if seg_code == code:
                name = re.sub(
                    r"^\s*(?:(?:Starter|Advanced)\s+Decks?|スタートデッキ|スターターデッキ|アドバンスデッキ)\s*",
                    "", seg_text, flags=re.IGNORECASE,
                ).strip()
                if name:
                    title = name
                break
    elif code is not None:
        trailing_m = re.search(r"[\[【]" + re.escape(code) + r"[\]】]\s*$", title)
        if trailing_m:
            title = title[:trailing_m.start()].strip()

    return title or None, code


# ---------------------------------------------------------------------------
# Field regexes
#
# Notes carried over from earlier live-run fixes:
#   - The optional "(For Other regions)" parenthetical after "Release date"
#     is a bounded group, not an open-ended "[^:]*" (that was greedy and ate
#     forward to the next colon on empty-date pages).
#   - Capture groups are "(.*?)" (zero-or-more) so an immediately-empty
#     field comes back empty instead of skipping to a later stop token.
#
# NEW (7 Oct 2026): the release date is immediately followed by the price
# block's unit line before the MSRP label -- EN "February 17, 2023 1 Pack
# MSRP: 4.19 USD", JP "2022年9月30日(金) 6枚1パック メーカー希望小売価格 ¥198（税込）"
# (both confirmed live on ver11.php). Without a stop token for that unit
# line the date came back as "February 17, 2023 1 Pack". _UNIT_EN /
# _UNIT_JP stop the capture there.
# ---------------------------------------------------------------------------
_UNIT_EN = r"\s*\b\d+\s*(?:Packs?|Box(?:es)?|Sets?|Decks?|Cases?)\b"
_UNIT_JP = r"\s*\d+(?:枚\d+)?(?:パック|セット|BOX|ボックス|個|箱)"

# FIXED (7 Oct 2026, EN full run): trailing notes that sit right after the
# date with no "*" before them were being glued onto it:
#   "May, 2021 Please check with your local store for releases outside of the U.S"
#   "June, 2022 Available only at Premium Bandai USA"
#   "February 12, 2021 Release date varies by region / channel"
# These phrases are now stop tokens.
_DATE_NOTE_STOPS_EN = (
    r"|\s*Please check with your local store|\s*Available only|\s*Available at"
    r"|\s*Release dates? (?:may )?var(?:y|ies)|\s*may vary by region|\s*PRE-ORDER"
)

_PRE_RELEASE_DATE_RE = re.compile(
    r"Pre-Release date\s*(?:\([^)]{0,60}\))?\s*:?\s*(.*?)(?=\s*Release date|\s*MSRP|\s*Card List|\s*Card types|\s*Rarity\b|\s*Contents?\b|\s*\*|"
    + _UNIT_EN + _DATE_NOTE_STOPS_EN + r"|$)",
    re.IGNORECASE,
)
_RELEASE_DATE_RE = re.compile(
    r"Release date\s*(?:\([^)]{0,60}\))?\s*:?\s*(.*?)(?=\s*MSRP|\s*Card List|\s*Card types|\s*Rarity\b|\s*Contents?\b|\s*\*|\s*Scheduled shipping|"
    + _UNIT_EN + _DATE_NOTE_STOPS_EN + r"|$)",
    re.IGNORECASE,
)

# FIXED (7 Oct 2026, EN full run): event-exclusive goods (Fest sleeves,
# playmats, Premium Card Collections) have NO release-date field at all --
# confirmed live on e.g. official_sleeve_alphamon.php, memory_boost_set.php.
# Their only "Release date" text is the generic footnote "Release date may
# vary by region. Price is a reference point... BANDAI CARD GAMES Fest 24-25
# World Tour", which passed the old has-a-digit check (the "24-25") and came
# back as the date. A real date always has a 4-digit year (EN) or 年 (JP),
# so candidates without one are rejected. The event line ("Available at BCG
# Fest 25-26 in Dallas!" / "event commemorative item of BANDAI CARD GAMES Fest
# 23-24 World Tour in Los Angeles") is captured separately as availability.
_REAL_DATE_RE = re.compile(r"(?:19|20)\d{2}|年")
_AVAILABILITY_RE = re.compile(
    r"(Available (?:only )?at [^!*。]{3,80}?)(?:!|\s*\*|\s*MSRP|$)"
    r"|event commemorative item of ([^.*]{3,100})",
    re.IGNORECASE,
)

_MSRP_RE = re.compile(r"MSRP\s*:?\s*\$?([\d,]+\.?\d*)\s*([A-Z]{3})?", re.IGNORECASE)
# BUG FOUND AND FIXED (7 Oct 2026): field order varies on EN too -- BT-27
# lists Contents BEFORE Rarity (confirmed live, /products/pack/ver27/), and
# this regex only stopped at "Photos and actual products"/"Resources"/"Card
# List", so Contents swallowed the whole rarity line and the "*Also includes
# alternate art..." note. Now stops at every other field label and at "*"
# (the start of an EN footnote), mirroring the JP fix for BT-27.
# FIXED (7 Oct 2026, EN full run):
#   - "Contents?" -- PB-21 (Rise of Digimon) labels it "Content" (singular).
#   - Deck pages glue two boilerplate sentences onto the contents ("This
#     product is a pre-built deck and the contents... This product contains
#     multiple identical cards..."), and PB-24 appends "Premium Bandai USA
#     Link" -- all three are now stop tokens.
_CONTENTS_RE = re.compile(
    r"\bContents?\b\s*:?\s*(.*?)(?=\s*Photos and actual products|\s*Resources\b|\s*Card List\b|\s*Questionnaire\b|\s*\*|"
    r"\s*\bRarity\b|\s*\bCard types\b|\s*(?:Pre-)?Release date|\s*\bMSRP\b|"
    r"\s*This product (?:is a pre-built|contains multiple)|\s*Premium Bandai USA|"
    # ST16 (found via --debug): an errata notice "Apology The ST16-11
    # WereGarurumon card ... was printed with incorrect text" follows the
    # contents with no "*" before it.
    r"\s*\bApology\b|\s*\bNotice\b|$)",
    re.IGNORECASE,
)
# Case-sensitive on purpose: these are the field LABELS ("Card types",
# "Rarity"), and marketing copy uses lowercase "rarity" in running text.
_CARD_TYPES_RE = re.compile(
    r"\bCard types\b\s*:?\s*(.*?)(?=\s*\bRarity\b|\s*\bContents?\b|\s*\*|\s*Release date|\s*MSRP|\s*Photos and actual|$)"
)
# FIXED (7 Oct 2026, EN full run): EX-05 etc. have no "*" before "Also
# includes alternate art..." / "Each display box contains 3 box toppers", so
# both were glued onto the rarity line. Stopped there now; the box-topper
# count is real pack-configuration info, so it's captured on its own below.
_RARITY_RE = re.compile(
    r"\bRarity\b\s*:?\s*(.*?)(?=\s*\*|\s*\bContents?\b|\s*Release date|\s*MSRP|\s*\bCard types\b|\s*Photos and actual|\s*Card List|"
    r"\s*Also includes|\s*Each (?:display )?box contains|$)"
)
_BOX_TOPPER_RE = re.compile(r"(Each (?:display )?box contains \d+ box toppers?)", re.IGNORECASE)

# JP labels: 発売日 (release date), (メーカー)希望小売価格 / 販売価格 (price),
# 種類数 (card types), レアリティ (rarity), 内容 (contents). Field order varies
# across pages (BT-27 lists 内容 first), so every stop list includes all the
# other labels.
# JP FIXED (7 Oct 2026, JP full run): Premium Bandai / limited JP pages have
# no 発売日 at all -- their only date is "発送時期 2026年2月発送予定" (shipping
# period), followed by a "ご購入はこちら" / "販売ページはこちら" link (confirmed
# live on tamer-goods-set_04.php, 3rd_anniversary_set.php). With no stop
# token for any of those, the ship date was glued onto the PRICE ("6,600円
# （税込） 発送時期 2026年2月発送予定 ご購入はこちら") and onto contents. They are
# all stop tokens now, and 発送時期 is captured as the release date when the
# page has no 発売日 (kept as-is, e.g. "2022年6月発送予定", so it's clear it's
# a ship month, not a street date).
_JP_STOPS = (
    r"|\s*発売日|\s*先行発売日|\s*(?:メーカー)?希望小売価格|\s*販売価格|\s*種類数|\s*レアリティ"
    r"|\s*内容|\s*カードリスト|\s*※|\s*アンケート|\s*発送時期|\s*予約"
    r"|\s*ご購入はこちら|\s*販売ページはこちら|\s*このwebサイト"
)
_RELEASE_DATE_JP_RE = re.compile(
    r"(?<!先行)発売日\s*:?\s*(.*?)(?=" + _UNIT_JP + _JP_STOPS + r"|$)"
)
_PRE_RELEASE_DATE_JP_RE = re.compile(
    r"先行発売日\s*:?\s*(.*?)(?=" + _UNIT_JP + _JP_STOPS + r"|$)"
)
_SHIP_DATE_JP_RE = re.compile(
    r"発送時期\s*:?\s*(.*?)(?=" + _UNIT_JP + _JP_STOPS + r"|$)"
)
_MSRP_JP_RE = re.compile(
    r"(?:(?:メーカー)?希望小売価格|販売価格)\s*:?\s*(.*?)(?=\s*\(?※" + _JP_STOPS + r"|$)"
)
_CONTENTS_JP_RE = re.compile(
    r"内容\s*(.*?)(?=\s*\(?※" + _JP_STOPS + r"|$)"
)
_CARD_TYPES_JP_RE = re.compile(
    r"種類数\s*:?\s*(.*?)(?=\s*\(?※" + _JP_STOPS + r"|$)"
)
_RARITY_JP_RE = re.compile(
    r"レアリティ\s*:?\s*(.*?)(?=\s*\(?※" + _JP_STOPS + r"|$)"
)
# JP FIXED: EX-01..EX-03 JP pages have no 内容 field at all -- the pack
# configuration only appears in the price label, "12枚1パック メーカー希望小売
# 価格 ¥440（税込）" (confirmed live on ex-01.php). Used as contents only when
# no 内容 is found.
_PACK_CONFIG_JP_RE = re.compile(r"(\d+枚\d+パック)\s*(?:メーカー)?希望小売価格")

# Any captured value longer than this means the regex ran into a section it
# has no stop token for (e.g. EX-06 JP's deck-recipe article) -- dropped to
# None and left for manual check rather than kept as obviously-wrong data.
_MAX_FIELD_LEN = 300


def _first_digit_match(pattern, text, must_match=None, max_len=None):
    """Scans every match and returns the first whose captured value contains
    a digit (and, if must_match is given, also matches that regex). Skips
    label occurrences in banners/disclaimers (e.g. ST-14 JP's "発売日変更のお
    知らせ" notice, or the "...希望小売価格になります。" footnote) that aren't
    real values."""
    for m in pattern.finditer(text):
        # A dangling "(" is left when a note like "(*North America ...)"
        # starts right after the date and its "*" is a stop token -- seen on
        # EX-03, BT09 and GIFT BOX ("November 11, 2022 (").
        candidate = m.group(1).strip().rstrip(".").rstrip("(（ ").strip()
        if not any(ch.isdigit() for ch in candidate):
            continue
        if must_match is not None and not must_match.search(candidate):
            continue
        if max_len is not None and len(candidate) > max_len:
            continue
        return m, candidate
    return None, None


def _short(value):
    if value and len(value) > _MAX_FIELD_LEN:
        return None
    return value or None


def extract_fields(own_content):
    # Pull out the pre-release date first and blank its matched span, so
    # the plain "Release date" search below can't re-match inside it.
    working = own_content
    pre_release_date = None
    m, candidate = _first_digit_match(_PRE_RELEASE_DATE_RE, working, _REAL_DATE_RE)
    if m is None:
        m, candidate = _first_digit_match(_PRE_RELEASE_DATE_JP_RE, working, _REAL_DATE_RE)
    if m:
        pre_release_date = candidate or None
        working = working[:m.start()] + " " * (m.end() - m.start()) + working[m.end():]

    release_date = None
    m, candidate = _first_digit_match(_RELEASE_DATE_RE, working, _REAL_DATE_RE)
    if m is None:
        m, candidate = _first_digit_match(_RELEASE_DATE_JP_RE, working, _REAL_DATE_RE)
    if m:
        release_date = candidate or None
    if release_date is None:
        # JP Premium Bandai / limited pages: ship month is the only date.
        _, release_date = _first_digit_match(_SHIP_DATE_JP_RE, own_content, _REAL_DATE_RE)

    availability = None
    m = _AVAILABILITY_RE.search(own_content)
    if m:
        availability = (m.group(1) or m.group(2) or "").strip() or None

    box_topper = None
    m = _BOX_TOPPER_RE.search(own_content)
    if m:
        box_topper = m.group(1)

    price = None
    m = _MSRP_RE.search(own_content)
    if m:
        amount = m.group(1)
        currency = m.group(2) or "USD"
        price = f"{amount} {currency}"
    else:
        # JP price kept as raw text ("¥198（税込）") -- no conversion guessed.
        _, candidate = _first_digit_match(_MSRP_JP_RE, own_content)
        price = candidate

    # First match with a digit in it -- real contents always state a count
    # ("1 Booster Pack contains 12 cards", "65 sleeves", "カード6枚入り"), so
    # a stray "contents" word in marketing copy is skipped.
    # BUG FOUND AND FIXED (7 Oct 2026, via --debug on BT-26 / EX-13): newer
    # pack pages have a "Contents | Open" page-navigation tab at the very top
    # ("Contents Contents Open POINT1 ... POINT6 ..."). That was the FIRST
    # "Contents" match, it ran past the length guard and was dropped -- and
    # the search stopped there instead of moving on to the real Contents
    # field further down. Over-long candidates are now skipped and the scan
    # continues to the next "Contents" on the page.
    _, contents = _first_digit_match(_CONTENTS_RE, own_content, max_len=_MAX_FIELD_LEN)
    if contents is None:
        _, contents = _first_digit_match(_CONTENTS_JP_RE, own_content, max_len=_MAX_FIELD_LEN)
    # Recorded so a full run can say WHY contents is empty: never found on the
    # page vs. found but every match ran past the real field into text with
    # no stop token (needs a new stop token -- see --debug).
    if contents is None:
        m = _PACK_CONFIG_JP_RE.search(own_content)
        if m:
            contents = m.group(1)
    contents_dropped_len = None
    if contents is None:
        _, too_long = _first_digit_match(_CONTENTS_RE, own_content)
        if too_long is None:
            _, too_long = _first_digit_match(_CONTENTS_JP_RE, own_content)
        if too_long:
            contents_dropped_len = len(too_long)

    # FIXED (9 Oct 2026, via --debug on ST-23 JP): marketing copy above the
    # spec block uses the same words ("新レアリティ・URとしてBT25に収録！"). That
    # first match ran to the end of the page, was dropped by the length guard,
    # and the real レアリティ field below was never reached. Over-long
    # candidates are now skipped and the scan moves on, same as Contents.
    _, card_types = _first_digit_match(_CARD_TYPES_RE, own_content, max_len=_MAX_FIELD_LEN)
    if card_types is None:
        _, card_types = _first_digit_match(_CARD_TYPES_JP_RE, own_content, max_len=_MAX_FIELD_LEN)

    _, rarity = _first_digit_match(_RARITY_RE, own_content, max_len=_MAX_FIELD_LEN)
    if rarity is None:
        _, rarity = _first_digit_match(_RARITY_JP_RE, own_content, max_len=_MAX_FIELD_LEN)

    # JP FIXED (8 Oct 2026, JP run): ST-18 and ST-20 JP put the rarity list
    # straight under 種類数 with no レアリティ label, so card types came back as
    # "・コモン：5種 ・アンコモン：5種 ..." and rarity was empty. A card-types
    # value that is really a rarity list is moved to rarity.
    if rarity is None and card_types and re.search(r"コモン|Common", card_types):
        rarity, card_types = card_types, None

    return {
        "release_date": release_date,
        "pre_release_date": pre_release_date,
        "price": price,
        "contents": contents,
        "card_types": _short(card_types),
        "rarity": _short(rarity),
        "box_topper": box_topper,
        "availability": availability,
        "contents_dropped_len": contents_dropped_len,
    }


# ---------------------------------------------------------------------------
# Image (NEW, 7 Oct 2026)
#
# Confirmed live on EN + JP ver11.php: the package shot is
#   <img src="../../images/products/pack/ver11/img_pkg.png" alt="PACKAGE">
# (src may be relative), inside the page's own content. Resolved against the
# page URL with urljoin so relative paths become absolute.
#
# Fallback if a page has no img_pkg: the product's own thumbnail in the
# sidebar widget (a.k.a. the "<a href=...this product...><img src=thumb>"
# entry). That's a small thumbnail rather than the full package shot, so
# image_is_thumbnail is set and the product is listed at the end of a full
# run for a manual look. No image is ever invented -- None if neither exists.
# ---------------------------------------------------------------------------
_IMG_PKG_RE = re.compile(r'<img[^>]+src="([^"]*img_pkg[^"]*)"', re.IGNORECASE)
_IMG_ALT_PACKAGE_RE = re.compile(
    r'<img(?=[^>]*\balt="(?:PACKAGE|パッケージ)")[^>]*\bsrc="([^"]+)"', re.IGNORECASE
)
# FIXED (7 Oct 2026, EN full run): the two big Premium Bandai landing pages
# (PB-21 tamers-evolution-box/, PB-24 nexus-of-digimon/) use a different
# template with no img_pkg -- their product shot is image_product.png /
# image_product.webp (confirmed live).
_IMG_PRODUCT_RE = re.compile(r'<img[^>]+src="([^"]*/image_product\.[a-z]+[^"]*)"', re.IGNORECASE)


def _code_key(code):
    """'ST-19' / 'ST19' / 'st-19' -> 'st19' for comparing against image paths."""
    return re.sub(r"[^a-z0-9]", "", (code or "").lower())


def extract_image(own_html, full_html, page_url, link, code=None):
    # FIXED (14 Oct 2026, EN+JP live runs): shared two-deck pages (ST18/ST19,
    # ST-20/ST-21) carry one package image PER deck --
    #   /images/products/deck/st-18/img_pkg.png  alt "... GUARDIAN VORTEX [ST18]"
    #   /images/products/deck/st-19/img_pkg.png  alt "... FABLE WALTZ [ST19]"
    # (confirmed from --debug image lists). Taking the first img_pkg gave ST19
    # and ST-21 their partner deck's picture. When a page has several img_pkg
    # images, the one whose folder or alt text names THIS product's code wins.
    if code:
        key = _code_key(code)
        for tag in re.findall(r"<img[^>]*img_pkg[^>]*>", own_html, re.IGNORECASE):
            src = re.search(r'src="([^"]+)"', tag)
            alt = re.search(r'alt="([^"]*)"', tag)
            folder = re.search(r"/([^/]+)/img_pkg", src.group(1)) if src else None
            if src and ((folder and _code_key(folder.group(1)) == key)
                        or (alt and re.search(r"[\[【]" + re.escape(code) + r"[\]】]", alt.group(1)))):
                return urljoin(page_url, src.group(1)), False
    for pattern in (_IMG_PKG_RE, _IMG_ALT_PACKAGE_RE, _IMG_PRODUCT_RE):
        m = pattern.search(own_html)
        if m:
            return urljoin(page_url, m.group(1)), False

    # Sidebar thumbnail fallback. The sidebar may write the link absolute or
    # relative, with or without a trailing slash (JP deck links drop it).
    link_core = re.escape(link.rstrip("/"))
    thumb_re = re.compile(
        r'<a[^>]+href="[^"]*' + link_core + r'/?"[^>]*>\s*<img[^>]+src="([^"]+)"',
        re.IGNORECASE,
    )
    m = thumb_re.search(full_html)
    if m:
        return urljoin(page_url, m.group(1)), True
    return None, False


def _get_with_retry(url, attempts=3):
    for i in range(attempts):
        try:
            return session.get(url, timeout=20)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if i == attempts - 1:
                raise
            time.sleep(2 * (i + 1))


def fetch_product(base_url, link):
    url = base_url + link
    # FIXED (9 Oct 2026, JP run): the site occasionally drops a connection
    # mid-run ("RemoteDisconnected" on ex-02.php), which skipped that product
    # entirely. Retried up to 3 times with a short pause before giving up.
    resp = _get_with_retry(url)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    raw_html = resp.text

    # Restrict field extraction to this page's OWN content -- see FOOTER CUT.
    own_html, cut_found = _cut_own_html(raw_html)
    own_content = _clean_text(own_html)
    if not cut_found:
        own_content = _cut_own_text(own_content)

    title, code = extract_title_and_code(raw_html, link)
    fields = extract_fields(own_content)
    image_url, image_is_thumbnail = extract_image(own_html, raw_html, url, link, code)

    category_match = _CATEGORY_RE.search(link)
    category = _CATEGORY_LABELS.get(category_match.group(1), "Other") if category_match else "Other"

    needs_human_check = not fields["release_date"] and not fields["price"]

    return {
        "title": title or link,
        "code": code,
        "category": category,
        **fields,
        "image_url": image_url,
        "image_is_thumbnail": image_is_thumbnail,
        "source_url": url,
        "needs_human_check": needs_human_check,
        # Kept for --debug only (see bottom of file): the exact flattened
        # text the field regexes ran against, plus whether the sidebar
        # marker was found.
        "_own_content": own_content,
        "_own_html": own_html,
        "_cut_found": cut_found,
        "_final_url": resp.url.rstrip("/"),
    }


def get_all_products(language):
    base_url = SOURCES[language]
    links = fetch_all_product_links(base_url)
    products = []
    seen = set()
    for link in links:
        try:
            product = fetch_product(base_url, link)
        except requests.exceptions.RequestException as e:
            print(f"  [WARN] Failed to fetch {link}: {e}")
            continue
        # Second guard against the JP ST-25/ST-26 duplicates: two hub links
        # that land on the same page after redirects count once.
        # BUG FIXED (8 Oct 2026, JP run): keying on the final URL alone also
        # dropped ST-19 and ST-21, which share ONE page with ST-18 / ST-20
        # but are separate products. Key is now (final URL, set code), so a
        # shared page still yields one product per code.
        key = (product["_final_url"], product["code"])
        if key in seen:
            continue
        seen.add(key)
        apply_event_date(language, product)
        products.append(product)
    return products


# ---------------------------------------------------------------------------
# Event-exclusive release dates (NEW, 14 Oct 2026)
#
# Event goods have no release-date field on Bandai's product pages, only an
# "Available at BANDAI CARD GAMES Fest ..." line. Their real dates were
# looked up on Bandai's own event / Fest product pages and are filled in
# here ONLY when the product page itself gives no date. Each rule: language,
# words that must ALL appear in the title (lowercase), date, note, source.
# The date used is the FIRST day the item went on sale (first Fest stop
# that sold it).
# ---------------------------------------------------------------------------
_EVENT_DATES = [
    # BCG Fest 23-24 World Tour, Los Angeles (Digimon event page: "January 27 - 28, 2024"; pre-order, on-site pickup)
    ("EN", ["sleeves", "gold"], "January 27, 2024", "BCG Fest 23-24, Los Angeles (27-28 Jan 2024)",
     "https://world.digimoncard.com/event/bcgfest_23-24/"),
    ("EN", ["sleeves", "silver"], "January 27, 2024", "BCG Fest 23-24, Los Angeles (27-28 Jan 2024)",
     "https://world.digimoncard.com/event/bcgfest_23-24/"),
    ("EN", ["fest 23-24", "playmat"], "January 27, 2024", "BCG Fest 23-24, Los Angeles (27-28 Jan 2024)",
     "https://world.digimoncard.com/event/bcgfest_23-24/"),
    # BCG Fest 24-25, Dallas -- Fest product pages state "Release date: September 14, 2024"
    ("EN", ["animation series 25th", "sleeve"], "September 14, 2024", "BCG Fest 24-25, Dallas (first stop)",
     "https://www.bandaicardgames-fest.com/24-25/en/dallas/products/02_26.html"),
    ("EN", ["memory boost"], "September 14, 2024", "BCG Fest 24-25, Dallas (first stop)",
     "https://www.bandaicardgames-fest.com/24-25/en/dallas/products/02_25.html"),
    # BCG Fest 25-26, Dallas -- Fest product pages state "Release date: September 20, 2025"
    ("EN", ["training set"], "September 20, 2025", "BCG Fest 25-26, Dallas",
     "https://www.bandaicardgames-fest.com/25-26/en/dallas/products/001.html"),
    ("EN", ["ver. omnimon"], "September 20, 2025", "BCG Fest 25-26, Dallas",
     "https://www.bandaicardgames-fest.com/25-26/en/dallas/products/006.html"),
    ("EN", ["ver. alphamon"], "September 20, 2025", "BCG Fest 25-26, Dallas",
     "https://www.bandaicardgames-fest.com/25-26/en/dallas/products/007.html"),
    # BCG Fest 26-27 -- first stop Jakarta, 10-11 Oct 2026; its product pages list
    # these four but give no date, so the first day of that stop is used.
    ("EN", ["scramble set"], "October 10, 2026", "BCG Fest 26-27, first stop Jakarta (10-11 Oct 2026); sale page gives no date",
     "https://www.bandaicardgames-fest.com/26-27/en/jakarta/products/405.html"),
    ("EN", ["gallantmon", "crimson mode"], "October 10, 2026", "BCG Fest 26-27, first stop Jakarta (10-11 Oct 2026); sale page gives no date",
     "https://www.bandaicardgames-fest.com/26-27/en/jakarta/products/407.html"),
    ("EN", ["imperialdramon", "paladin mode"], "October 10, 2026", "BCG Fest 26-27, first stop Jakarta (10-11 Oct 2026); sale page gives no date",
     "https://www.bandaicardgames-fest.com/26-27/en/jakarta/products/408.html"),
    ("EN", ["26-27", "playmat"], "October 10, 2026", "BCG Fest 26-27, first stop Jakarta (10-11 Oct 2026); sale page gives no date",
     "https://www.bandaicardgames-fest.com/26-27/en/jakarta/products/406.html"),
    # JP
    ("JP", ["lm-08"], "2026年8月9日", "DC-1グランプリ 2026 SEASON 1 (event period 9-23 Aug 2026, 6 stores); first day used",
     "https://digimoncard.com/event/2026/dc1-gp/season1/"),
    ("JP", ["超!テイマーバトル2022"], "2022年10月11日", "Bandai's digimon.net product page: 「2022年10月11日発売」",
     "https://digimon.net/product/detail.php?item_type=1&id=593"),
    # LM-09 deliberately NOT dated: the JP page only says 「2026年度のイベントにて販売予定」
    # (to be sold at a FY2026 event) -- no JP date announced as of 14 Oct 2026.
    # (Premium Bandai USA's 27 Nov 2026 date is the EN release, not JP.)
]


def apply_event_date(language, product):
    """Fill a missing release date from _EVENT_DATES; records the source."""
    product["release_date_source"] = None
    if product.get("release_date"):
        return
    # Title AND set code: the JP LM-08 page title is just
    # 「リミテッドカードパック FINAL CREST」 -- the code is a separate field
    # (fixed 14 Oct 2026, live JP run).
    title = f"{product.get('title') or ''} {product.get('code') or ''}".lower().replace("！", "!")
    for lang, words, date, note, source in _EVENT_DATES:
        if lang == language and all(w in title for w in words):
            product["release_date"] = date
            product["release_date_source"] = f"{note} -- {source}"
            return


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
        print(f"  {i}. [{m['category']}] {m['title']} [{m['code']}]")
    choice = input(f"Enter number (1-{len(matches)}), or blank to cancel: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(matches)):
        print("Cancelled.")
        return None
    return matches[int(choice) - 1]


def build_record(language, product):
    contents = []
    if product["contents"]:
        contents.append(product["contents"])
    if product["card_types"]:
        contents.append(f"Card types: {product['card_types']}")
    if product["rarity"]:
        contents.append(f"Rarity: {product['rarity']}")
    if product["box_topper"]:
        contents.append(product["box_topper"])
    if product["pre_release_date"]:
        contents.append(f"Pre-release date: {product['pre_release_date']}")
    # Event-exclusive goods have no release-date field -- the event line is
    # the only release info the page gives, so it's surfaced here instead
    # of being forced into the date field.
    if not product["release_date"] and product["availability"]:
        contents.append(f"Release: no date on page -- {product['availability']}")
    if product.get("release_date_source"):
        contents.append(f"Release date source (not on product page): {product['release_date_source']}")

    # A shared dual-product page can leave the chosen code already embedded
    # mid-title -- only append "[code]" if it isn't already present.
    title = product["title"]
    if product["code"] and f"[{product['code']}]" not in title and f"【{product['code']}】" not in title:
        title = f"{title} [{product['code']}]"

    return make_product_record(
        game=f"Digimon Card Game ({language})",
        title=title,
        sku=product["code"],  # Bandai's own set/product code (e.g. ST-1, BT-27, PB-12E), not a barcode/UPC
        release_date=product["release_date"],
        image_url=product["image_url"],
        contents=contents,
        pack_configuration=[],
        price=product["price"],
        source_url=product["source_url"],
    )


def discover_all(language):
    products = get_all_products(language)
    no_date = [p for p in products if not p["release_date"]]
    no_contents = [p for p in products if not p["contents"]]
    no_image = [p for p in products if not p["image_url"]]
    thumb_only = [p for p in products if p["image_is_thumbnail"]]

    print(f"[DEBUG] Found {len(products)} {language} products:")
    for p in products:
        print(f"  - [{p['category']}] {p['title']} [{p['code']}]")

    if no_date:
        print(f"\n{len(no_date)} product(s) with no release date:")
        for p in no_date:
            note = f"  ({p['availability']})" if p["availability"] else ""
            print(f"  - {p['title']}  {p['source_url']}{note}")
    if no_contents:
        print(f"\n{len(no_contents)} product(s) with no contents text:")
        for p in no_contents:
            why = (f"  (FOUND but dropped: {p['contents_dropped_len']} chars -- ran past the field)"
                   if p["contents_dropped_len"] else "  (no Contents field found on page)")
            print(f"  - {p['title']}  {p['source_url']}{why}")
    if no_image:
        print(f"\n{len(no_image)} product(s) with no image found:")
        for p in no_image:
            print(f"  - {p['source_url']}")
    if thumb_only:
        print(f"\n{len(thumb_only)} product(s) using sidebar THUMBNAIL (no img_pkg on page):")
        for p in thumb_only:
            print(f"  - {p['source_url']}")

    records = []
    for p in products:
        record = build_record(language, p)
        print_product_record(record)
        records.append(record)
    return records


def discover(language, search_term):
    products = get_all_products(language)
    matches = search_products(products, search_term)

    if not matches:
        print(f"No product found matching '{search_term}' in {language} catalog")
        return

    match = resolve_match(matches, search_term)
    if match is None:
        return

    record = build_record(language, match)
    print_product_record(record)

    if _DEBUG:
        print("\n[DEBUG] every <img> in this page's own content (src | alt):")
        for tag in re.findall(r"<img[^>]*>", match.get("_own_html") or "", re.IGNORECASE):
            src = re.search(r'src="([^"]+)"', tag)
            alt = re.search(r'alt="([^"]*)"', tag)
            print(f"  {src.group(1) if src else '?'}  |  {alt.group(1) if alt else ''}")
        print("\n[DEBUG] sidebar marker found:", match["_cut_found"])
        print("[DEBUG] text the field regexes ran against:")
        print(match["_own_content"])
    return record


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        lang_arg = sys.argv[1].strip().upper()
        if lang_arg not in SOURCES:
            print(f"Unknown language '{lang_arg}'. Use EN or JP.")
            sys.exit(1)
        if len(sys.argv) >= 3:
            discover(lang_arg, sys.argv[2])
        else:
            discover_all(lang_arg)
    else:
        lang_input = input("Language (EN or JP): ").strip().upper()
        while lang_input not in SOURCES:
            lang_input = input("Please enter EN or JP: ").strip().upper()
        search_term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        if search_term:
            discover(lang_input, search_term)
        else:
            discover_all(lang_input)