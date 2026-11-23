"""
Discovery script for Pokémon TCG sealed products, ENGLISH + JAPANESE.

JP  (official): pokemon-card.com's product search API /products/resultAPI.php
    (found in /assets/js/products/bundle.js, pokemon_diag4.py, 10 Nov 2026).
    Every product type on the site (拡張パック, 構築デッキ, その他の商品,
    周辺グッズ), 1996 to today, with official image, exact date and contents.
    Contents = description lines, shop notes (※ lines, 【取り扱い店舗】) dropped.

EN  (INTERIM, WITH GAPS): pokemon.com blocks automated requests (Imperva
    "Pardon Our Interruption" on every request, 10 Nov 2026) and its terms
    forbid bulk downloading; TCGplayer / Star City Games terms ban scraping.
    Open, licensed sources are used until the supervisor confirms an image
    source (distributor feed / permission):
      pkmjson  https://github.com/ArthurG-data/pkmjson   data: CC BY 4.0
               (credit required; most EN contents came from TCGplayer
               product text via tcgcsv.com -- flagged to the supervisor)
      TCGdex   https://api.tcgdex.net   MIT -- set release dates, promo card names
    EN date = product's own pkmjson date; else a hand-researched date with its
    source (_EN_DATE_FALLBACK, 11 Nov 2026); else the set's TCGdex date (noted);
    none for remaining products filed under promo sets (svp, swshp, ...).
    EN image = blank on purpose. EN leaves out cases and "Set of N" groupings.

Products releasing after today are skipped (same rule as the other games).

Usage:
    python src/discovery_pokemon.py all --out=pokemon_output.txt
    python src/discovery_pokemon.py all --lang=jp --out=pokemon_jp.txt
    python src/discovery_pokemon.py all --lang=en --out=pokemon_en.txt
    python src/discovery_pokemon.py "Surging Sparks|ハドウシーカー" --out=pokemon_search.txt
    --refresh  re-download pkmjson/TCGdex data instead of using pkm_cache/
    --debug    extra detail on searched products
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


def _flag(name):
    if name in sys.argv:
        sys.argv.remove(name)
        return True
    return False


_REFRESH = _flag("--refresh")
_DEBUG = _flag("--debug")
_LANGS = ["en", "jp"]
if any(a.startswith("--lang=") for a in sys.argv):
    _lang_arg = next(a for a in sys.argv if a.startswith("--lang="))
    sys.argv.remove(_lang_arg)
    _LANGS = [_lang_arg.split("=", 1)[1].lower()]

import html as html_lib
import io
import json
import os
import re
import time
import zipfile
from collections import Counter
from datetime import date

import requests
from common import make_product_record, print_product_record


# ================================================================ ENGLISH
PKMJSON_ZIP = "https://codeload.github.com/ArthurG-data/pkmjson/zip/refs/heads/main"
PKMJSON_WEB = "https://github.com/ArthurG-data/pkmjson/blob/main/"
TCGDEX = "https://api.tcgdex.net/v2/en"
CACHE = "pkm_cache"
CREDIT = "pkmjson by ArthurG-data (CC BY 4.0); set dates and card names from TCGdex (MIT)"
IMAGE_NOTE = ("left blank on purpose: no allowed source has English product images "
              "(pokemon.com blocks scraping); pending supervisor (distributor feed / permission)")
_PROMO_SETS = re.compile(r"^(basep|np|dpp|hgssp|bwp|xyp|smp|swshp|svp|mep|wp)$|p$")

en_session = requests.Session()
en_session.headers.update({"User-Agent": "tcg-migration discovery (intern project)"})

# EN products with no date in pkmjson, filed under promo sets: release dates
# researched by hand on 11 Nov 2026 (exact dates only; month-only or "early/late"
# dates were NOT used). Source per product. Used only when pkmjson has no date.
_EN_DATE_FALLBACK = {
    "2016 World Championship Deck: Cody Walinski (Ninja Blitz)": ("2016-11-09", "https://ccgcastle.com/news/2016-11-07-world-championship-decks-2016"),
    "2016 World Championship Deck: Jesper Eriksen (Bebe Deck)": ("2016-11-09", "https://ccgcastle.com/news/2016-11-07-world-championship-decks-2016"),
    "2016 World Championship Deck: Shintaro Ito (Magical Symphony)": ("2016-11-09", "https://ccgcastle.com/news/2016-11-07-world-championship-decks-2016"),
    "2016 World Championship Deck: Shunto Sadahiro (Black Dragon)": ("2016-11-09", "https://ccgcastle.com/news/2016-11-07-world-championship-decks-2016"),
    "Legendary Beasts Collector Pin 3 Pack Blister [Entei]": ("2017-05-01", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Legendary Beasts Collector Pin 3 Pack Blister [Raikou]": ("2017-05-03", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Legendary Beasts Collector Pin 3 Pack Blister [Suicune]": ("2017-05-03", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "General Mills Promo Booster Pack [Sun & Moon]": ("2017-08-22", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "2017 World Championship Deck: Diego Cassiraga (Infinite Force)": ("2017-11-17", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "2017 World Championship Deck: Kabu Fukase (Samurai Sniper)": ("2017-11-17", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "2017 World Championship Deck: Naoto Suzuki (Golisodor)": ("2017-11-17", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "2017 World Championship Deck: Zachary Bokhari (Ice Path FTW)": ("2017-11-17", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Marshadow Box (International Version)": ("2017-11-17", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Premium Trainer's XY Collection": ("2017-11-17", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Sun & Moon Trainer Kit: Alolan Sandslash & Alolan Ninetales": ("2018-06-01", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Pikachu & Eevee Poke Ball Collection": ("2018-11-02", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "2018 World Championship Deck: Magnus Pedersen (Garbanette)": ("2018-11-16", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "2018 World Championship Deck: Naohito Inoue (Buzzroc)": ("2018-11-16", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "2018 World Championship Deck: Pedro Eugenio Torres (Dragones Y Sombras)": ("2018-11-16", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "2018 World Championship Deck: Robin Schulz (Victory Map)": ("2018-11-16", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "General Mills Promo Booster Pack [Kanto]": ("2019-01-15", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Friends Mini Tin [Bulbasaur]": ("2019-03-01", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Friends Mini Tin [Charmander]": ("2019-03-01", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Friends Mini Tin [Eevee]": ("2019-03-01", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Friends Mini Tin [Pikachu]": ("2019-03-01", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Friends Mini Tin [Squirtle]": ("2019-03-01", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Let's Play Theme Deck [Eevee]": ("2019-05-03", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Let's Play Theme Deck [Pikachu]": ("2019-05-03", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Power Mini Tin [Charizard]": ("2019-09-06", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Power Mini Tin [Dragonite]": ("2019-09-06", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Power Mini Tin [Mew]": ("2019-09-06", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Power Mini Tin [Mewtwo]": ("2019-09-06", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Kanto Power Mini Tin [Vulpix]": ("2019-09-06", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Fall 2019 Collector's Chest Tin": ("2019-11-22", "https://bulbapedia.bulbagarden.net/wiki/TAG_TEAM_Tins"),
    "Meowth VMAX Special Collection (International Version)": ("2020-01-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Galar Pal Mini Tin [Grookey & Wooloo]": ("2020-04-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Galar Pal Mini Tin [Scorbunny & Pikachu]": ("2020-04-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Galar Pal Mini Tin [Sobble & Rookidee]": ("2020-04-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Galar Pal Mini Tin [Yamper & Morpeko]": ("2020-04-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Pokemon TCG: Trainer's Toolkit (2020)": ("2020-06-26", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Collector's Pin Two Pack Blisters [Charizard]": ("2020-07-24", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Collector's Pin Two Pack Blisters [Mimikyu]": ("2020-07-24", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Powers Tin [Eevee V] (International Version)": ("2020-09-04", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Powers Tin [Eternatus V] (International Version)": ("2020-09-04", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Powers Tin [Pikachu V] (International Version)": ("2020-09-04", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Snorlax & Lapras VMAX Collection": ("2020-10-16", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "League Battle Deck [Zacian V]": ("2020-11-20", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Legends of Johto Pin Collection": ("2020-11-27", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Collector's Pin Two Pack Blisters [Espeon]": ("2020-12-01", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Collector's Pin Two Pack Blisters [Umbreon]": ("2020-12-01", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Battle Deck Bundle [Venusaur V/Blastoise V]": ("2021-02-05", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Battle Deck [Blastoise V]": ("2021-02-05", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Battle Deck [Venusaur V]": ("2021-02-05", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "General Mills 25th Anniversary Booster Pack": ("2021-02-27", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Blastoise VMAX Battle Box": ("2021-03-19", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Venusaur VMAX Battle Box": ("2021-03-19", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Battle Deck Bundle [Victini V/Gardevoir V]": ("2021-05-07", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Battle Deck [Gardevoir V]": ("2021-05-07", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Battle Deck [Victini V]": ("2021-05-07", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Mighty Mysterious Tin [Darkrai]": ("2021-08-06", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Mighty Mysterious Tin [Mimikyu]": ("2021-08-06", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Mighty Mysterious Tin [Snorlax]": ("2021-08-06", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Eevee Evolutions Tin [Flareon V]": ("2021-09-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Eevee Evolutions Tin [Flareon V] (Live Code Card)": ("2021-09-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Eevee Evolutions Tin [Jolteon V]": ("2021-09-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Eevee Evolutions Tin [Jolteon V] (Live Code Card)": ("2021-09-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Eevee Evolutions Tin [Vaporeon V]": ("2021-09-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Eevee Evolutions Tin [Vaporeon V] (Live Code Card)": ("2021-09-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Pikachu & Zekrom GX Premium Collection": ("2021-11-01", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Reshiram & Charizard GX Premium Collection": ("2021-11-01", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "League Battle Deck [Rapid Strike Urshifu VMAX]": ("2021-11-12", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "League Battle Deck [Single Strike Urshifu VMAX]": ("2021-11-12", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Flareon VMAX Premium Collection": ("2021-12-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Jolteon VMAX Premium Collection": ("2021-12-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Vaporeon VMAX Premium Collection": ("2021-12-03", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "2 Pack Blister [Tornadus, Thundurus & Landorus]": ("2022-01-11", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Knock Out Collection [Toxtricity, Duraludon, Sandaconda]": ("2022-01-21", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Pokemon Stacking Tin: Electric": ("2022-02-25", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Pokemon Stacking Tin: Grass": ("2022-02-25", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Pokemon Stacking Tin: Water": ("2022-02-25", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Pikachu V Box": ("2022-03-25", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Pokemon TCG: Battle Academy 2022": ("2022-03-25", "https://bulbapedia.bulbagarden.net/wiki/Battle_Academy_2022_(TCG)"),
    "Lucario VSTAR Premium Collection": ("2022-04-08", "https://zulusgames.com/collections/pokemon-fusion-strike/products/pokemon-tcg-lucario-vstar-premium-collection"),
    "V Heroes Tin [Espeon V] (International Version)": ("2022-04-08", "https://www.pokeguardian.com/826832_v-heroes-tin-spring-2022-revealed (European version)"),
    "V Heroes Tin [Sylveon V] (International Version)": ("2022-04-08", "https://www.pokeguardian.com/826832_v-heroes-tin-spring-2022-revealed (European version)"),
    "V Heroes Tin [Umbreon V] (International Version)": ("2022-04-08", "https://www.pokeguardian.com/826832_v-heroes-tin-spring-2022-revealed (European version)"),
    "V Heroes Tin [Espeon V]": ("2022-04-22", "https://www.pokeguardian.com/826832_v-heroes-tin-spring-2022-revealed (US version)"),
    "V Heroes Tin [Sylveon V]": ("2022-04-22", "https://www.pokeguardian.com/826832_v-heroes-tin-spring-2022-revealed (US version)"),
    "V Heroes Tin [Umbreon V]": ("2022-04-22", "https://www.pokeguardian.com/826832_v-heroes-tin-spring-2022-revealed (US version)"),
    "Spring 2022 Collector Chest": ("2022-05-06", "https://pokeguardian.com/847343_pokemon-tcg-collector-chest-spring-2022-revealed-legends-arceus-first-partner-promos"),
    "Professor Juniper Premium Tournament Collection Box": ("2022-05-20", "https://pokeguardian.com/858387_professor-juniper-premium-tournament-collection-revealed"),
    "Kleavor VSTAR Premium Collection (International)": ("2022-05-27", "https://www.pokeguardian.com/864224_kleavor-vstar-premium-collection-coming"),
    "2 Booster Packs & Jirachi Collector's Pin": ("2022-07-01", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Collector's Pin Two Pack Blisters [Jirachi]": ("2022-07-01", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "2 Booster Packs & Celebi Collector's Pin": ("2022-09-16", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Checklane 2-Pack Pin Blister [Celebi]": ("2022-09-16", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "V Battle Deck Bundle [Deoxys V / Zeraora V]": ("2022-10-14", "https://zulusgames.com/collections/trading-card-games/products/pokemon-tcg-v-battle-deck-zeraora-v-and-deoxys-v"),
    "V Battle Deck [Deoxys V]": ("2022-10-14", "https://zulusgames.com/collections/trading-card-games/products/pokemon-tcg-v-battle-deck-zeraora-v-and-deoxys-v"),
    "V Battle Deck [Zeraora V]": ("2022-10-14", "https://zulusgames.com/collections/trading-card-games/products/pokemon-tcg-v-battle-deck-zeraora-v-and-deoxys-v"),
    "Play! Pokemon Prize Pack Series One": ("2022-11-09", "https://bulbapedia.bulbagarden.net/wiki/Play!_Pok\u00e9mon_Prize_Pack_Series_One_(TCG)"),
    "Knock Out Collection [Galarian Slowking, Galarian Obstagoon, and Dragapult]": ("2023-01-06", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Mimikyu ex Showcase": ("2023-03-03", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paldea Legends Tin [Koraidon ex]": ("2023-06-09", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paldea Legends Tin [Miraidon ex]": ("2023-06-09", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Knock Out Collection [Shaymin, Zeraora, and Marshadow]": ("2023-07-12", "https://bulbapedia.bulbagarden.net/wiki/Boltund_V_Box"),
    "Enhanced 2-Pack Blister Pack [Armarouge, Revavroom & Houndstone]": ("2023-08-04", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Play! Pokemon Prize Pack Series Three": ("2023-08-14", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Deluxe Battle Deck [Quaquaval ex]": ("2023-09-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paldea Partners Tin [Meowscarada ex]": ("2023-09-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paldea Partners Tin [Meowscarada ex] [International Edition]": ("2023-09-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paldea Partners Tin [Quaquaval ex]": ("2023-09-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paldea Partners Tin [Quaquaval ex] [International]": ("2023-09-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paldea Partners Tin [Skeledirge ex]": ("2023-09-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paldea Partners Tin [Skeledirge ex] [International Edition]": ("2023-09-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Trick or Trade BOOster Bundle 2023 (120 ct)": ("2023-09-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Trick or Trade BOOster Bundle 2023 (50 ct)": ("2023-09-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paradox Powers ex Special Collection": ("2023-09-08", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Evolving Powers Premium Collection (Target Exclusive)": ("2023-09-22", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "ex Battle Deck [Greninja ex]": ("2023-10-06", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "ex Battle Deck [Kangaskhan ex]": ("2023-10-06", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Charizard ex Premium Collection": ("2023-10-20", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Gyarados ex Premium Collection": ("2023-11-03", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Oinkologne ex Box": ("2023-11-03", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Fall 2023 Collector Chest": ("2023-11-17", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Pokemon Trading Card Game Classic": ("2023-11-17", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Checklane 2-Pack Blister [Pawmot] (1-tab)": ("2024-01-05", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Checklane 2-Pack Blister [Pawmot] (3-tab)": ("2024-01-05", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Knock Out Collection [Iron Jugulis]": ("2024-01-05", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise (Scarlet & Violet Knock Out Collection)"),
    "Play! Pokemon Prize Pack Series Four": ("2024-02-14", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Mabosstiff ex Showcase": ("2024-02-23", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "2023 World Championship Deck: Gabriel Fernandez (Colorless Lugia)": ("2024-03-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "2023 World Championship Deck: Shao Tong Yen (Lost Box Kyogre)": ("2024-03-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "2023 World Championship Deck: Tord Reklev (Psychic Elegance)": ("2024-03-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "2023 World Championship Deck: Vance Kelley (Mew's Revenge)": ("2024-03-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paldea Adventure Chest": ("2024-03-01", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Pokemon Stacking Tin: Dragon": ("2024-03-01", "https://www.pokeguardian.com/1590138_pokemon-tcg-stacking-tin-q1-2024-revealed"),
    "Pokemon Stacking Tin: Metal": ("2024-03-01", "https://www.pokeguardian.com/1590138_pokemon-tcg-stacking-tin-q1-2024-revealed"),
    "Pokemon Stacking Tin: Psychic": ("2024-03-01", "https://www.pokeguardian.com/1590138_pokemon-tcg-stacking-tin-q1-2024-revealed"),
    "Grafaiai ex Box": ("2024-04-05", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Iono Premium Tournament Collection Box": ("2024-04-05", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Enhanced 2-Pack Blister Pack [Palafin, Baxcalibur & Garganacl]": ("2024-04-11", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paradox Clash Tin [Iron Leaves ex]": ("2024-05-24", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Paradox Clash Tin [Walking Wake ex]": ("2024-05-24", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Play! Pokemon Prize Pack Series Five": ("2024-08-14", "https://bulbapedia.bulbagarden.net/wiki/Play!_Pok%C3%A9mon_Prize_Pack_Series_Five_(TCG)"),
    "Tera Brawlers Premium Collection": ("2024-08-16", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Trick or Trade BOOster Bundle 2024 (120 ct)": ("2024-08-30", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Trick or Trade BOOster Bundle 2024 (35 mini packs)": ("2024-08-30", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Trick or Trade BOOster Bundle 2024 (80 mini packs)": ("2024-08-30", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Trick or Trade BOOster Bundle 2024 - Mini Booster Pack": ("2024-08-30", "https://bulbapedia.bulbagarden.net/wiki/Scarlet_%26_Violet_TCG_Series_merchandise"),
    "Pokemon - Poke Ball Tin - Great Ball (Q4 2024)": ("2024-10-18", "https://pokeguardian.com/1968024_new-poke-ball-tins-moon-ball-q4-2024-revealed"),
    "Pokemon - Poke Ball Tin - Moon Ball (Q4 2024)": ("2024-10-18", "https://pokeguardian.com/1968024_new-poke-ball-tins-moon-ball-q4-2024-revealed"),
    "Pokemon - Poke Ball Tin - Poke Ball (Q4 2024)": ("2024-10-18", "https://pokeguardian.com/1968024_new-poke-ball-tins-moon-ball-q4-2024-revealed"),
    "Pokemon - Poke Ball Tin - Premier Ball (Q4 2024)": ("2024-10-18", "https://pokeguardian.com/1968024_new-poke-ball-tins-moon-ball-q4-2024-revealed"),
    "Pokemon - Poke Ball Tin - Ultra Ball (Q4 2024)": ("2024-10-18", "https://pokeguardian.com/1968024_new-poke-ball-tins-moon-ball-q4-2024-revealed"),
    "Greninja ex Ultra-Premium Collection": ("2024-11-01", "https://snkrdunk.com/en/magazine/2024/08/22/pokemon-tcg-greninja-ex-ultra-premium-collection-release-date-price-where-to-buy/"),
    "Meddling Sparks Premium Collection": ("2024-11-01", "https://snkrdunk.com/en/magazine/2024/11/04/pokemon-tcg-meddling-sparks-premium-collection-release-date-price-where-to-buy"),
    "Knock Out Collection [Alakazam]": ("2025-01-03", "https://www.pokeguardian.com/2170085_pokemon-tcg-knock-out-collection-chien-pao-or-alakazam-revealed"),
    "Knock Out Collection [Chien-Pao]": ("2025-01-03", "https://www.pokeguardian.com/2170085_pokemon-tcg-knock-out-collection-chien-pao-or-alakazam-revealed"),
    "Play! Pokemon Prize Pack Series Six": ("2025-02-14", "https://www.pokeguardian.com/2320289_play-pokemon-prize-pack-series-six-revealed"),
    "Azure Legends Tin [Dialga ex] (International Version)": ("2025-02-26", "https://snkrdunk.com/en/magazine/2024/12/10/pokemon-tcg-azure-legends-tins-release-date-price-where-to-buy/"),
    "Azure Legends Tin [Kyogre ex] (International Version)": ("2025-02-26", "https://snkrdunk.com/en/magazine/2024/12/10/pokemon-tcg-azure-legends-tins-release-date-price-where-to-buy/"),
    "Azure Legends Tin [Xerneas ex] (International Version)": ("2025-02-26", "https://snkrdunk.com/en/magazine/2024/12/10/pokemon-tcg-azure-legends-tins-release-date-price-where-to-buy/"),
    "Lillie Premium Tournament Collection Box": ("2025-04-25", "https://snkrdunk.com/en/magazine/2025/01/15/pokemon-tcg-premium-tournament-collection-release-date-price-where-to-buy"),
    "Masks of Ogerpon Premium Collection": ("2025-06-13", "https://www.pokeguardian.com/2457988_masks-of-ogerpon-premium-collection-revealed"),
    "Charizard ex Special Collection": ("2025-06-27", "https://zulusgames.com/collections/trading-card-games/products/pokemon-tcg-charizard-ex-special-collection"),
    "Mimikyu ex & Alcremie ex Premium Collection": ("2025-10-17", "https://www.pokeguardian.com/2712638_mimikyu-ex-alcremie-ex-premium-collection-revealed"),
    "Mega Charizard X ex Ultra Premium Collection": ("2025-11-14", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "Fall 2025 Collector Chest": ("2025-12-05", "https://www.pokeguardian.com/2678085_pokemon-tcg-collector-chest-fall-2025-revealed-with-foil-cards-of-ralts-kirlia-and-riolu"),
    "Pokemon - Poke Ball Tin - Great Ball (Q4 2025)": ("2025-12-05", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "Pokemon - Poke Ball Tin - Poke Ball (Q4 2025)": ("2025-12-05", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "Pokemon - Poke Ball Tin - Premier Ball (Q4 2025)": ("2025-12-05", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "Pokemon - Poke Ball Tin - Repeat Ball (Q4 2025)": ("2025-12-05", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "Pokemon - Poke Ball Tin - Ultra Ball (Q4 2025)": ("2025-12-05", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "2-Pack Blister [Raikou] (1-tab)": ("2026-01-01", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "2-Pack Blister [Raikou] (3-tab)": ("2026-01-01", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "Play! Pokemon Prize Pack Series Eight": ("2026-01-01", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "Pokemon Day 2026 Collection": ("2026-01-30", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "First Partner Collection 2026 Series 1 Booster Pack": ("2026-03-20", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise (First Partner Illustration Collection Series 1)"),
    "2-Pack Blister Pack [Oddish, Gloom, & Vileplume]": ("2026-03-27", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "2025 World Championship Deck: Jose Cruz Galindo-Resendiz (Pult Bomb)": ("2026-04-03", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "2025 World Championship Deck: Liao Fu Guan (Joltdengo)": ("2026-04-03", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "2025 World Championship Deck: Riley McKay (Flutter Devo Gardevoir)": ("2026-04-03", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "2025 World Championship Deck: Yuyu Okita (JP Raging Bolt)": ("2026-04-03", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise"),
    "First Partner Collection 2026 Series 2 Booster Pack": ("2026-06-19", "https://m.bulbapedia.bulbagarden.net/wiki/Mega_Evolution_TCG_Series_merchandise (First Partner Illustration Collection Series 2)"),
    "Play! Pokemon Prize Pack Series Nine": ("2026-07-01", "https://pokeguardian.com/3216348_play-pokemon-prize-pack-series-nine-revealed"),
    "Mega Greninja ex Premium Collection": ("2026-07-03", "https://pokeguardian.com/3123364_mega-greninja-ex-premium-collection-revealed"),
}



# ---------------------------------------------------------------- downloads
def _get(url, binary=False):
    last = None
    for attempt in range(3):
        try:
            r = en_session.get(url, timeout=120)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content if binary else r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def load_pkmjson():
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "pkmjson.zip")
    if _REFRESH or not os.path.exists(path):
        raw = _get(PKMJSON_ZIP, binary=True)
        if not raw:
            raise RuntimeError("could not download pkmjson")
        with open(path, "wb") as f:
            f.write(raw)
    families = {}
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            m = re.search(r"/(data/international/[^/]+/[^/]+\.json)$", n)
            if m:
                d = json.loads(z.read(n).decode("utf-8"))
                d["_path"] = m.group(1)
                families[d["id"]] = d
    return families


def _json_cache(name):
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and not _REFRESH:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(name, data):
    with open(os.path.join(CACHE, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def set_dates(set_ids):
    cache = _json_cache("tcgdex_sets.json")
    for sid in sorted(set_ids):
        if sid in cache:
            continue
        try:
            d = _get(f"{TCGDEX}/sets/{sid}")
            cache[sid] = {"name": (d or {}).get("name"), "releaseDate": (d or {}).get("releaseDate")}
        except RuntimeError as e:
            cache[sid] = {"name": None, "releaseDate": None, "error": str(e)}
        time.sleep(0.1)
    _save_cache("tcgdex_sets.json", cache)
    return cache


def card_names(card_ids):
    cache = _json_cache("tcgdex_cards.json")
    for cid in sorted(card_ids):
        if cid in cache:
            continue
        try:
            d = _get(f"{TCGDEX}/cards/{cid}")
            cache[cid] = (d or {}).get("name")
        except RuntimeError:
            cache[cid] = None
        time.sleep(0.1)
    _save_cache("tcgdex_cards.json", cache)
    return cache


# ---------------------------------------------------------------- formatting
def excluded_reason(fam, name):
    if fam.get("category") == "case" or re.search(r"\bCase\s*$", name, re.I):
        return "case"
    if re.search(r"\bSet of \d+\b", name, re.I):
        return "Set of N grouping"
    return None


def render_contents(contents, families, cards, set_info):
    lines = []
    for s in contents.get("sealed", []):
        inner = families.get(s.get("product"), {})
        en = next((r["name"] for r in inner.get("releases", []) if r.get("language") == "en"), None)
        label = en or inner.get("name") or s.get("product")
        if inner.get("category") in ("booster_pack", "sleeved_booster_pack") and "pack" not in label.lower():
            label += " booster pack"
        lines.append(f"{s.get('count', 1)} x {label}")
    for c in contents.get("cards", []):
        cid = c.get("tcgdexCardId", "?")
        nm = cards.get(cid)
        role = c.get("role")
        lines.append(f"{c.get('count', 1)} x {nm + ' ' if nm else ''}card ({cid})"
                     + (f" [{role}]" if role else ""))
    for o in contents.get("other", []):
        lines.append(f"{o.get('count', 1)} x {o.get('name', '?')}")
    pool = contents.get("pool") or []
    if pool:
        names = [(set_info.get(p["set"]) or {}).get("name") or p["set"] for p in pool]
        lines.append("Cards from: " + ", ".join(names))
    return lines


# ---------------------------------------------------------------- discovery
def en_get_all_products():
    families = load_pkmjson()
    en = []
    for fam in families.values():
        for r in fam.get("releases", []):
            if r.get("language") == "en":
                en.append((fam, r))
    set_info = set_dates({fam["set"] for fam, _ in en}
                         | {p["set"] for fam, _ in en for p in (fam.get("contents") or {}).get("pool", [])})
    cards = card_names({c["tcgdexCardId"] for fam, r in en
                        for c in ((r.get("contents") or fam.get("contents") or {}).get("cards", []))
                        if c.get("tcgdexCardId")})
    today = date.today().isoformat()
    products, skipped, excluded = [], [], Counter()
    for fam, r in en:
        name = r.get("name") or fam.get("name")
        why = excluded_reason(fam, name)
        if why:
            excluded[why] += 1
            continue
        own = r.get("releaseDate")
        sid = fam["set"]
        info = set_info.get(sid) or {}
        if own:
            rel, how = own, "product release date (pkmjson)"
        elif name in _EN_DATE_FALLBACK:
            rel, src_url = _EN_DATE_FALLBACK[name]
            how = f"researched release date (source: {src_url})"
        elif _PROMO_SETS.search(sid):
            rel, how = None, f"no product date in pkmjson; filed under promo set '{sid}', so no set-date fallback"
        elif info.get("releaseDate"):
            rel, how = info["releaseDate"][:10], f"set release date of {info.get('name') or sid} (TCGdex); pkmjson has no product date"
        else:
            rel, how = None, "no date in pkmjson or TCGdex"
        if rel and rel > today:
            skipped.append((name, f"releases {rel}"))
            continue
        contents = r.get("contents") or fam.get("contents") or {}
        products.append({
            "name": name,
            "set": info.get("name") or sid,
            "set_code": sid,
            "category": fam.get("category"),
            "release_date": rel,
            "release_how": how,
            "contents": render_contents(contents, families, cards, set_info),
            "status": fam.get("contentsStatus"),
            "sources": r.get("sources") or [],
            "source_url": PKMJSON_WEB + fam["_path"],
        })
    unique, keys = [], set()
    for p in products:
        k = (re.sub(r"[^a-z0-9]", "", p["name"].lower()), p["release_date"])
        if k in keys:
            continue
        keys.add(k)
        unique.append(p)
    unique.sort(key=lambda p: (p["release_date"] or "9999", p["name"]))
    en_get_all_products.excluded = excluded
    return unique, skipped


def en_build_record(p):
    return make_product_record(
        game="Pokémon TCG (EN)",
        title=p["name"],
        sku=None,
        release_date=p["release_date"],
        image_url=None,
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["source_url"],
    )


def en_print_notes(p):
    print(f"Set: {p['set']} ({p['set_code']}) | Category: {p['category']}")
    print(f"Release note: {p['release_how']}" if p["release_date"]
          else f"Release note: NO DATE -- {p['release_how']}")
    print(f"Image note: {IMAGE_NOTE}")
    if p["status"] == "partial":
        print("Contents note: pkmjson marks these contents as partial")
    print(f"Credit: {CREDIT}")




# ================================================================ JAPANESE
BASE = "https://www.pokemon-card.com"
API = BASE + "/products/resultAPI.php"
jp_session = requests.Session()
jp_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
    "Referer": BASE + "/products/index.html",
})


# ---------------------------------------------------------------- fetching
def fetch_page(page):
    last = None
    for attempt in range(3):
        try:
            r = jp_session.get(API, params={"page": page}, timeout=30)
            r.raise_for_status()
            data = r.json()
            if str(data.get("result")) != "1":
                raise ValueError(f"API error: {data.get('errMsg')}")
            return data
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"page {page}: {last}")


def fetch_all():
    first = fetch_page(1)
    max_page = int(first.get("maxPage") or 1)
    hit_cnt = int(first.get("hitCnt") or 0)
    items, problems = list(first.get("products") or []), []
    for page in range(2, max_page + 1):
        time.sleep(0.5)
        try:
            items += fetch_page(page).get("products") or []
        except RuntimeError as e:
            problems.append(str(e))
    return items, hit_cnt, max_page, problems


# ---------------------------------------------------------------- parsing
_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_MONTH = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")


def parse_date(text):
    text = text or ""
    m = _DATE.search(text)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d).isoformat(), "official release date (pokemon-card.com)"
        except ValueError:
            pass
    m = _MONTH.search(text)
    if m:
        return None, f"month only on the site: '{text.strip()}'"
    return None, f"no date on the site: '{text.strip()}'"


def clean_lines(desc):
    s = desc or ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s).replace("　", " ")
    return [re.sub(r"\s+", " ", x).strip() for x in s.split("\n")]


def contents_from(desc):
    out = []
    for line in clean_lines(desc):
        if not line:
            continue
        if line.startswith("【"):        # 【取り扱い店舗に関して】 etc: shop notes follow
            break
        if line.startswith(("※", "＊", "*")):
            continue
        line = line.lstrip("・･ ").strip()
        if line:
            out.append(line)
    return out


def absolute(url):
    url = (url or "").strip()
    if not url:
        return None
    return url if url.startswith("http") else BASE + ("" if url.startswith("/") else "/") + url


def norm(s):
    return re.sub(r"[\s「」『』・･\-–—]", "", (s or "")).lower()


def jp_get_all_products():
    raw, hit_cnt, max_page, problems = fetch_all()
    today = date.today().isoformat()
    products, skipped, seen = [], [], set()
    for it in raw:
        name = html_lib.unescape((it.get("productTitle") or "").strip())
        rel, how = parse_date(it.get("releaseDate"))
        img = absolute(it.get("tumbsImg"))
        key = (norm(name), rel or it.get("releaseDate"), img)
        if key in seen:
            continue
        seen.add(key)
        if rel and rel > today:
            skipped.append((name, f"releases {rel}"))
            continue
        detail = absolute(it.get("link_detailPage"))
        products.append({
            "name": name,
            "type": (it.get("productType") or "").strip(),
            "release_date": rel,
            "release_how": how,
            "image": img,
            "contents": contents_from(it.get("description")),
            "stores": (it.get("storesAvailable") or "").strip(),
            "price_txt": (it.get("priceTxt") or "").strip(),
            "source_url": detail or BASE + "/products/index.html",
            "pc_url": absolute(it.get("link_pokemonCenter")),
            "raw_desc": it.get("description") or "",
        })
    # same product listed twice with a different image file: keep the first
    unique, keys = [], set()
    for p in products:
        k = (norm(p["name"]), p["release_date"])
        if k in keys:
            continue
        keys.add(k)
        unique.append(p)
    unique.sort(key=lambda p: (p["release_date"] or "9999", p["name"]))
    jp_get_all_products.stats = {"hit_cnt": hit_cnt, "max_page": max_page, "fetched": len(raw)}
    return unique, skipped, problems


def jp_build_record(p):
    return make_product_record(
        game="Pokémon TCG (JP)",
        title=p["name"],
        sku=None,
        release_date=p["release_date"],
        image_url=p["image"],
        contents=p["contents"],
        pack_configuration=[],
        price=None,
        source_url=p["source_url"],
    )


def jp_print_notes(p):
    print(f"Type: {p['type']}" + (f" | Sold at: {p['stores']}" if p["stores"] else ""))
    print(f"Release note: {p['release_how']}" if p["release_date"]
          else f"Release note: NO DATE -- {p['release_how']}")
    if not p["image"]:
        print("Image note: no image in the site's product search")




# ================================================================ combined
def get_all_products():
    products, skipped, problems, notes = [], [], [], []
    if "en" in _LANGS:
        try:
            ps, sk = en_get_all_products()
            for p in ps:
                p["lang"] = "en"
            products += ps
            skipped += [("en", n, w) for n, w in sk]
            exc = getattr(en_get_all_products, "excluded", {})
            hows = Counter("own date" if p["release_how"].startswith("product") else
                           "researched date" if p["release_how"].startswith("researched") else
                           "set date" if p["release_how"].startswith("set") else "no date" for p in ps)
            notes.append(f"EN left out per scope: " + (", ".join(f"{k} {v}" for k, v in exc.items()) or "none"))
            notes.append("EN dates: " + ", ".join(f"{k} {v}" for k, v in hows.items()))
        except RuntimeError as e:
            problems.append(("en", "pkmjson/TCGdex", str(e)))
    if "jp" in _LANGS:
        try:
            ps, sk, pr = jp_get_all_products()
            for p in ps:
                p["lang"] = "jp"
            products += ps
            skipped += [("jp", n, w) for n, w in sk]
            problems += [("jp", "resultAPI.php", w) for w in pr]
            st = getattr(jp_get_all_products, "stats", {})
            notes.append(f"JP site reports {st.get('hit_cnt')} products on {st.get('max_page')} pages; fetched {st.get('fetched')}")
        except RuntimeError as e:
            problems.append(("jp", "resultAPI.php", str(e)))
    return products, skipped, problems, notes


def build_record(p):
    return en_build_record(p) if p["lang"] == "en" else jp_build_record(p)


def print_notes(p):
    en_print_notes(p) if p["lang"] == "en" else jp_print_notes(p)


def discover_all():
    products, skipped, problems, notes = get_all_products()
    for lang in _LANGS:
        mine = [p for p in products if p["lang"] == lang]
        years = sorted(p["release_date"][:4] for p in mine if p["release_date"])
        key = "category" if lang == "en" else "type"
        print(f"[DEBUG] Found {len(mine)} Pokémon {lang.upper()} products"
              + (f", {years[0]} to {years[-1]}" if years else ""))
        print(f"[DEBUG]   by {key}: " + ", ".join(f"{c} {n}" for c, n in Counter(p[key] for p in mine).most_common()))
    for n in notes:
        print(f"[DEBUG] {n}")
    if "en" in _LANGS:
        print(f"[DEBUG] EN credit: {CREDIT}")
    for p in products:
        print_product_record(build_record(p))
        print_notes(p)
    print(f"\n{len(skipped)} item(s) skipped as not released yet:")
    for lang, n, why in skipped:
        print(f"  - [{lang.upper()}] {n}: {why}")
    print(f"\n{len(problems)} problem(s):")
    for lang, n, why in problems:
        print(f"  - [{lang.upper()}] {n}: {why}")
    for label, test in (("no release date", lambda p: not p["release_date"]),
                        ("no image (JP only; EN images are blank on purpose)", lambda p: p["lang"] == "jp" and not p["image"]),
                        ("no contents", lambda p: not p["contents"])):
        bad = [p for p in products if test(p)]
        print(f"\n{len(bad)} product(s) with {label}:")
        for p in bad[:300]:
            print(f"  - [{p['lang'].upper()}] {p['name']}  {p['release_date'] or p['release_how']}")
        if len(bad) > 300:
            print(f"  ... and {len(bad) - 300} more")
    if "en" in _LANGS:
        n_en = sum(1 for p in products if p["lang"] == "en")
        n_part = sum(1 for p in products if p["lang"] == "en" and p.get("status") == "partial")
        print(f"\n[EN] {n_part} product(s) marked partial by pkmjson (mostly booster packs: random cards)")
        print(f"[EN] All {n_en} EN products have no image: {IMAGE_NOTE}")


def discover(term):
    products, skipped, _, _ = get_all_products()
    terms = [t.strip().lower() for t in term.split("|") if t.strip()]
    matches = [p for p in products if any(
        t in p["name"].lower() or (p["lang"] == "en" and (t in (p["set"] or "").lower() or t == p["set_code"].lower()))
        for t in terms)]
    if not matches:
        print(f"No product found matching '{term}'")
        for lang, n, why in skipped:
            if any(t in n.lower() for t in terms):
                print(f"  (skipped as not released yet: [{lang.upper()}] {n}, {why})")
    for p in matches:
        print_product_record(build_record(p))
        print_notes(p)
        if _DEBUG:
            if p["lang"] == "en":
                print("[DEBUG] pkmjson sources: " + " | ".join(p["sources"][:5]))
            else:
                print(f"[DEBUG] raw description: {p['raw_desc'][:1500]}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].strip().lower() != "all":
        discover(sys.argv[1])
    elif len(sys.argv) >= 2:
        discover_all()
    else:
        term = input("Product name to search (leave blank to pull all products): ").strip().strip('"').strip("'")
        discover(term) if term else discover_all()