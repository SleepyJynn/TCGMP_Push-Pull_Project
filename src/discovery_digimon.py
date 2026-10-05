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
  - BUG FOUND AND FIXED via a live run: every product page ends its own
    content with a shared "Booster Packs" cross-promo footer widget
    listing dozens of OTHER products (a sitewide "latest products"
    sidebar, not page-specific) -- and several of THOSE footer entries
    also use the literal "DIGIMON CARD GAME <name> [<code>]" phrasing
    (not just the page's own heading), so a global search for that phrase
    across the whole page sometimes matched garbage from the footer
    instead of the real title, and the release-date regex likewise
    sometimes matched the literal phrase "Release date" inside unrelated
    footer/disclaimer text. Fixed by splitting the page's text on the
    first "Booster Packs" occurrence and running ALL field extraction
    (title, code, release date, price, contents) only against the text
    BEFORE that footer -- confirmed present on every sample checked
    across deck/goods/pack categories.
  - BUG #2 FOUND AND FIXED across two live runs: title/code extraction was
    first anchored on a second "DIGIMON CARD GAME <title>" repeat that only
    exists on some Deck/Goods page templates after the nav (every Booster
    Pack page states its title once, at the very top, so the whole pack
    category came back with a raw link as the title and no code). A second
    attempt anchored on a "PRODUCTS" nav breadcrumb within the flattened
    body text, which turned out to sit at inconsistent distances across
    templates (ST-15..ST-24 decks and several bracket-less Goods pages
    either failed outright or swallowed a whole marketing paragraph).
    Fixed for good by reading the page's own <title> tag directly (raw
    HTML, confirmed via a live dump to hold EXACTLY "DIGIMON CARD GAME
    <name>[ [<code>]] − PRODUCTS｜Digimon Card Game" with no surrounding
    noise, on every template checked) instead of guessing at body-text
    structure.
  - Also fixed: responses weren't reliably decoded as UTF-8 (requests
    guessed a wrong charset), producing mojibake like "ΓêÆ" / "┬⌐" in
    titles and contents text -- both the hub page and per-product fetches
    now force resp.encoding = "utf-8".
  - Not every product has a bracketed set code -- some plain goods items
    (e.g. "Official Card Sleeves Silver") have no code at all, correctly
    left as None rather than borrowing one from a nearby product.
  - Release date text is inconsistent in format across products -- full
    dates ("January 29th, 2021"), month+year only ("March, 2023"),
    "onwards" suffixes, and sometimes TWO regional dates ("Scheduled
    shipping date (For United States) ... Release date (For Other
    regions) ...") or a separate "Pre-Release date" field. This script
    captures the primary "Release date" text as-is (not reformatted/
    guessed into a single clean date), and separately captures a
    pre-release date when present, rather than silently picking one
    region over another.
  - MSRP is present on most pages ("MSRP : 9.99 USD", "MSRP: 100.00 USD",
    "MSRP 4.99 USD" -- inconsistent spacing/colon, handled loosely).
  - "Contents" (pack configuration / what's in the box) is free marketing
    prose, same situation as every other game's discovery script so far --
    no structured table, so the cleaned text is stored as-is rather than
    forced into a fabricated structured list.
  - A product page with no parseable Release date AND no MSRP is flagged
    for manual review rather than silently guessed at or dropped -- same
    "flag, don't fabricate" approach used for FAB's commerce-marker check.

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
# writing it to a file, which is why the .txt file was still garbled even
# after chcp 65001. Writing the file ourselves (explicit UTF-8, no shell
# text pipeline involved) sidesteps that entirely. Pass --out=<path> to use
# it, e.g.:
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

import re
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
    resp.encoding = "utf-8"  # the site doesn't declare charset reliably enough for requests to guess right -- confirmed via mojibake in a live run otherwise
    links = sorted(set(_PRODUCT_LINK_RE.findall(resp.text)))
    return links


def _clean_text(html):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Every product page ends its own content with a shared "Booster Packs"
# cross-promo footer widget listing dozens of OTHER products (confirmed on
# every sample checked, deck/goods/pack alike -- it's a sitewide "latest
# products" sidebar, not page-specific). That footer's text was colliding
# with every regex below (matching titles/dates from unrelated products),
# so everything is now extracted from the text BEFORE that footer only.
_FOOTER_SPLIT = "Booster Packs"

# JP BUG FOUND AND FIXED via a live run: JP has the exact same sitewide
# footer widget as EN (confirmed -- it lists every product under four
# category headings: 拡張パック/構築済みデッキ/その他/プレミアムバンダイ),
# but it has no literal "Booster Packs"-style marker, so JP fell back to
# the FULL page text. For pages with no 内容/種類数/レアリティ/カードリスト
# section of their own (single-accessory Goods pages like playmats), the
# MSRP/Contents regexes had no stop token before the footer and swallowed
# the entire multi-thousand-character footer dump instead of stopping
# empty. The footer widget's structure is a heading immediately followed by
# its own first list item restating the same heading text -- e.g. "拡張パッ
# ク 拡張パック ブースターパック IGNITION OF X【BT-27】..." -- which never
# occurs in real per-page content (where a category word appears once, not
# doubled back-to-back), so it's a safe, specific split marker.
_FOOTER_SPLIT_JP_RE = re.compile(r"拡張パック\s*拡張パック")

# BUG #2 (found via two live runs, fixed here for good): both previous
# approaches tried to locate the title within the page's FLATTENED VISIBLE
# TEXT, which turned out to have inconsistent structure across templates
# (a second repeat of the heading after nav, present on some page types but
# not others; a stray "PRODUCTS" match appearing at very different
# distances depending on template). Confirmed via a direct raw-HTML dump
# (inspect_digimon3.py) that there's a much more reliable source: every
# single page's own <title> tag (and matching og:title meta) holds EXACTLY
# "DIGIMON CARD GAME <name>[ [<code>]] − PRODUCTS｜Digimon Card Game" with
# no surrounding noise, on every template checked (old .php decks, new
# trailing-slash decks, bracket-less goods pages). Extracting straight from
# that tag sidesteps the whole flattened-text guessing game.
# JP BUG FOUND AND FIXED via a live run: the JP site (digimoncard.com) is
# correctly UTF-8 (confirmed via a raw-bytes diagnostic -- the garbled
# output a person sees is their own terminal's codepage misrendering
# correctly-decoded Japanese text on redirect, same as the EN mojibake
# issue, fixed the same way with `chcp 65001`), but it wraps set codes in
# full-width Japanese brackets -- "【ST-1】" -- not the ASCII "[ST-1]" used
# on the EN site. The bracket regex below accepts either style so the same
# code works for both languages.
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
    body-text structure. Not every product has a bracketed code (plain
    goods items like 'Official Card Sleeves Silver' don't) -- left as None
    rather than borrowed from a nearby product.

    A few products are released as a linked PAIR (e.g. ST18+ST19, ST20+
    ST21) sharing ONE marketing page -- both of their distinct product
    URLs (/deck/st-18/ and /deck/st-19/) return the exact same page, whose
    <title> mentions BOTH codes (e.g. "...[ST18]...[ST19]..."). Always
    taking the last bracket would silently give both URLs the same code,
    which is a real duplicate-SKU bug for a migration tool that needs one
    row per official product -- so when more than one bracket code is
    present, the one matching THIS product's own URL slug is used instead
    of just picking the last one."""
    m = _TITLE_TAG_RE.search(raw_html)
    if not m:
        return None, None
    title = _clean_text(m.group(1))

    if title.upper().startswith(_TITLE_PREFIX):
        title = title[len(_TITLE_PREFIX):].strip()

    suffix_m = _TITLE_SUFFIX_RE.search(title)
    if suffix_m:
        title = title[:suffix_m.start()].strip()

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

    if code is not None:
        # Only strip the code out of the title text if it's the trailing
        # bracket -- for a shared dual-product page the chosen code may
        # not be at the very end, in which case the combined title is left
        # as-is rather than guessing how to split it into two per-product
        # names.
        trailing_m = re.search(r"[\[【]" + re.escape(code) + r"[\]】]\s*$", title)
        if trailing_m:
            title = title[:trailing_m.start()].strip()

    return title or None, code


# BUG FOUND AND FIXED via a live run: many evergreen decks have a "Release
# date" field/label present on the page with NO actual date filled in --
# the very next thing in the flattened text is the "Card types .../Rarity
# ..." breakdown, not a date. The original stop-token list (MSRP/Card
# List/*/Scheduled shipping) never appears again after an empty release
# date on these pages, so the lazy capture ran all the way to the end of
# the page, swallowing the entire card-type/rarity/contents breakdown as
# if it were release-date text (e.g. "9.99 USD Card types 16 types
# Rarity..."). "Card types", "Rarity" and "Contents" are added as stop
# tokens so an empty field correctly comes back empty rather than full of
# unrelated product-breakdown text.
# NOTE: two separate bugs stacked here on a live run:
#   1. The "[^:]*" that used to sit right after "Release date" (meant to
#      allow an optional parenthetical like "Release date (For Other
#      regions)" before the colon) was GREEDY, so on an empty release-date
#      field it kept eating forward (through "Card types 16 types Rarity
#      Common") looking for the NEXT colon anywhere on the page. Replaced
#      with a bounded, explicit "(optional parenthetical)" group instead of
#      an open-ended "any non-colon character" run.
#   2. The capture group itself was "(.+?)" (one-or-more, lazy), which can
#      never match zero characters -- so even with the stop-token list
#      fixed, when a stop token sat IMMEDIATELY after "Release date" (an
#      empty field), the "+" forced it to consume at least one character
#      and skip past that first occurrence, only stopping at whatever
#      later occurrence of a stop word happened to exist. Changed to
#      "(.*?)" (zero-or-more) so an immediately-empty field correctly
#      comes back as an empty string rather than swallowing unrelated text.
_PRE_RELEASE_DATE_RE = re.compile(
    r"Pre-Release date\s*(?:\([^)]{0,60}\))?\s*:?\s*(.*?)(?=\s*Release date|\s*MSRP|\s*Card List|\s*Card types|\s*Rarity\b|\s*Contents\b|\s*\*|$)",
    re.IGNORECASE,
)
_RELEASE_DATE_RE = re.compile(
    r"Release date\s*(?:\([^)]{0,60}\))?\s*:?\s*(.*?)(?=\s*MSRP|\s*Card List|\s*Card types|\s*Rarity\b|\s*Contents\b|\s*\*|\s*Scheduled shipping|$)",
    re.IGNORECASE,
)
_MSRP_RE = re.compile(r"MSRP\s*:?\s*\$?([\d,]+\.?\d*)\s*([A-Z]{3})?", re.IGNORECASE)
_CONTENTS_RE = re.compile(
    r"\bContents\b\s*(.+?)(?=\s*Photos and actual products|\s*Resources\b|\s*Card List\b|$)",
    re.IGNORECASE,
)

# JP BUG FOUND AND FIXED via a live run: every one of these EN-only regexes
# is built around English field labels ("Release date", "MSRP", "Contents"),
# which never appear on the JP site -- its real labels are 発売日 (release
# date), メーカー希望小売価格 / 希望小売価格 (manufacturer's suggested
# retail price) and 内容 (contents), confirmed via a live raw-HTML sample
# (digimoncard.com/products/deck/st-1.php). That sample showed the exact
# real sequence "カードリスト 発売日 2020年4月24日 1セット メーカー希望小売
# 価格 ¥500+税 種類数 全16種 レアリティ ... 内容 構築済みデッキ 1セット
# （カード54枚） メモリーゲージ2枚 ※..." -- so 種類数 (card-type count) and
# レアリティ (rarity) are used as stop tokens for the same reason "Card
# types"/"Rarity" are on the EN side (an empty field must stop immediately,
# not swallow the following breakdown), and "※" (the conventional marker
# JP product pages use to start a disclaimer footnote) is the stop token
# for Contents, playing the same role "Photos and actual products..." does
# on EN pages. JP price text (e.g. "¥500+税") is kept as raw text rather
# than split into amount/currency like the EN "9.99 USD" -- the yen+tax
# convention doesn't map cleanly onto that shape, and guessing at a
# conversion would be fabricating data.
# JP BUG FOUND AND FIXED via a live run: some older Goods pages ("リミテッ
# ドカードセット2023", "デッキケースセット") use a third price label,
# 販売価格 ("sale price"), instead of (メーカー)希望小売価格 -- it wasn't a
# stop token anywhere, so Release date ran straight through it and swallowed
# the actual price text ("2023年4月発売 販売価格 1,500円（税込）" came back
# as the date), while MSRP never matched at all on those pages since it only
# looked for 希望小売価格. Both regexes below now also recognize 販売価格.
_RELEASE_DATE_JP_RE = re.compile(
    r"発売日\s*:?\s*(.*?)(?=\s*(?:メーカー)?希望小売価格|\s*販売価格|\s*種類数|\s*レアリティ|\s*内容\b|\s*カードリスト|\s*※|$)"
)
_PRE_RELEASE_DATE_JP_RE = re.compile(
    r"先行発売日\s*:?\s*(.*?)(?=\s*発売日|\s*(?:メーカー)?希望小売価格|\s*販売価格|\s*種類数|\s*レアリティ|\s*内容\b|\s*カードリスト|\s*※|$)"
)
_MSRP_JP_RE = re.compile(
    r"(?:(?:メーカー)?希望小売価格|販売価格)\s*:?\s*(.*?)(?=\s*種類数|\s*レアリティ|\s*内容\b|\s*カードリスト|\s*※|$)"
)
# JP BUG FOUND AND FIXED via a live run: BT-27's page lists 内容 BEFORE
# 希望小売価格/種類数/レアリティ (the reverse order from BT-26 and most other
# pages, where 内容 comes last), and this regex's stop tokens didn't include
# any of those three labels -- so on that page order, Contents swallowed
# straight through the price and rarity breakdown instead of stopping at
# 内容's own boundary. Now stops at any of the other field labels, not just
# the disclaimer/survey/card-list markers.
_CONTENTS_JP_RE = re.compile(
    r"内容\s*(.*?)(?=\s*発売日|\s*(?:メーカー)?希望小売価格|\s*販売価格|\s*種類数|\s*レアリティ\b|\s*※|\s*アンケート|\s*カードリスト|$)"
)


# BUG FOUND AND FIXED via a live run: ST-14's JP page has a "発売日変更のお
# 知らせ" ("notice of release-date change") banner near the top of the page,
# ahead of the real 発売日 field -- a plain .search() matches THAT "発売日"
# first and returns its banner/copyright text ("変更のお知らせ ©本郷あきよ
# し・フジテレビ・東映アニメーション") as if it were the date. A real date
# always has at least one digit in it (year, month or day), so this now
# scans every match on the page and takes the first one that actually looks
# like a date, skipping over label occurrences like this that don't.
def _first_digit_match(pattern, text):
    for m in pattern.finditer(text):
        candidate = m.group(1).strip().rstrip(".")
        if any(ch.isdigit() for ch in candidate):
            return m, candidate
    return None, None


def extract_fields(own_content):
    # Pull out the pre-release date first and blank its matched span, so
    # the plain "Release date" search below can't re-match inside it or
    # get confused by the two appearing back-to-back.
    working = own_content
    pre_release_date = None
    m, candidate = _first_digit_match(_PRE_RELEASE_DATE_RE, working)
    if m is None:
        m, candidate = _first_digit_match(_PRE_RELEASE_DATE_JP_RE, working)
    if m:
        pre_release_date = candidate or None
        working = working[:m.start()] + " " * (m.end() - m.start()) + working[m.end():]

    release_date = None
    m, candidate = _first_digit_match(_RELEASE_DATE_RE, working)
    if m is None:
        m, candidate = _first_digit_match(_RELEASE_DATE_JP_RE, working)
    if m:
        release_date = candidate or None

    price = None
    m = _MSRP_RE.search(own_content)
    if m:
        amount = m.group(1)
        currency = m.group(2) or "USD"
        price = f"{amount} {currency}"
    else:
        # JP BUG FOUND AND FIXED via a live run: some Goods pages (mostly
        # Premium Bandai exclusives with no listed retail price) only
        # contain the phrase "希望小売価格" inside a generic site-wide
        # disclaimer sentence ("...価格はメーカー希望小売価格になります。"),
        # with no actual number attached -- that disclaimer is the FIRST
        # (and often only) match, so a plain .search() was returning the
        # disclaimer's leftover fragment ("になります。") as if it were a
        # price. Now takes the first match that actually contains a digit,
        # i.e. a real price, and leaves price as None (flagged for manual
        # check) if no match on the page ever does -- consistent with
        # "flag, don't fabricate".
        for jp_m in _MSRP_JP_RE.finditer(own_content):
            candidate = jp_m.group(1).strip()
            if any(ch.isdigit() for ch in candidate):
                price = candidate
                break

    contents = None
    m = _CONTENTS_RE.search(own_content) or _CONTENTS_JP_RE.search(own_content)
    if m:
        contents = m.group(1).strip() or None
        # BUG FOUND AND FIXED via a live run: EX-06's JP page has a long
        # "deck recipe" strategy article (card-by-card writeup, a full deck
        # list table) sitting between 内容 and the next recognized stop
        # token, which none of this regex's stop tokens cover since that
        # article isn't a labeled field -- so Contents swallowed several
        # thousand characters of unrelated strategy text instead of the
        # real one-line package contents. A genuine Contents value is
        # always a short package list; anything this long is a sign the
        # regex ran into a section it has no stop token for, so it's
        # dropped to None (flagged for manual check) rather than keeping
        # obviously-wrong data -- "flag, don't fabricate".
        if contents and len(contents) > 300:
            contents = None

    return release_date, pre_release_date, price, contents


def fetch_product(base_url, link):
    url = base_url + link
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"  # same fix as the hub page -- requests was guessing the wrong charset, causing mojibake like "┬⌐" / "ΓêÆ" in titles and contents text
    raw_html = resp.text
    text = _clean_text(raw_html)

    # Restrict everything to this page's OWN content -- see _FOOTER_SPLIT
    # comment above. Tries the EN marker, then the JP marker, falling back
    # to the full text only if neither is ever found, rather than silently
    # extracting nothing.
    if _FOOTER_SPLIT in text:
        own_content = text.split(_FOOTER_SPLIT, 1)[0]
    else:
        jp_m = _FOOTER_SPLIT_JP_RE.search(text)
        own_content = text[:jp_m.start()] if jp_m else text

    # Title/code come from the raw <title> tag, not the flattened body text
    # -- see the comment on extract_title_and_code for why. The link is
    # passed through so a shared dual-product page (e.g. ST18+ST19) can be
    # disambiguated by its own URL slug instead of always picking the last
    # bracket code found.
    title, code = extract_title_and_code(raw_html, link)
    release_date, pre_release_date, price, contents = extract_fields(own_content)

    category_match = _CATEGORY_RE.search(link)
    category = _CATEGORY_LABELS.get(category_match.group(1), "Other") if category_match else "Other"

    needs_human_check = not release_date and not price

    return {
        "title": title or link,
        "code": code,
        "category": category,
        "release_date": release_date,
        "pre_release_date": pre_release_date,
        "price": price,
        "contents": contents,
        "source_url": url,
        "needs_human_check": needs_human_check,
    }


def get_all_products(language):
    base_url = SOURCES[language]
    links = fetch_all_product_links(base_url)
    products = []
    for link in links:
        try:
            products.append(fetch_product(base_url, link))
        except requests.exceptions.RequestException as e:
            print(f"  [WARN] Failed to fetch {link}: {e}")
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
    if product["pre_release_date"]:
        contents.append(f"Pre-release date: {product['pre_release_date']}")

    # A shared dual-product page (see extract_title_and_code) can leave the
    # chosen code already embedded mid-title (e.g. "...[ST18] FABLE WALTZ
    # [ST19]") when it wasn't the trailing bracket -- appending "[code]"
    # again there would duplicate it ("...[ST19] [ST18]"). Only append it
    # if it isn't already present in the title text.
    title = product["title"]
    if product["code"] and f"[{product['code']}]" not in title:
        title = f"{title} [{product['code']}]"

    return make_product_record(
        game=f"Digimon Card Game ({language})",
        title=title,
        sku=product["code"],  # Bandai's own set/product code (e.g. ST-1, BT-27, PB-12E), not a barcode/UPC
        release_date=product["release_date"],
        image_url=None,  # img_pkg.png path confirmed to exist per product but not yet wired up here
        contents=contents,
        pack_configuration=[],
        price=product["price"],
        source_url=product["source_url"],
    )


def discover_all(language):
    products = get_all_products(language)
    flagged = [p for p in products if p["needs_human_check"]]

    print(f"[DEBUG] Found {len(products)} {language} products:")
    for p in products:
        flag = "  <-- NO RELEASE DATE OR MSRP FOUND, please verify" if p["needs_human_check"] else ""
        print(f"  - [{p['category']}] {p['title']} [{p['code']}]{flag}")

    if flagged:
        print(f"\n{len(flagged)} product(s) flagged for manual check (no release date or MSRP parsed):")
        for p in flagged:
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