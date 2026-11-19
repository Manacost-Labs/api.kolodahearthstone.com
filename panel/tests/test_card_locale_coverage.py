from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import card_locale_coverage as cov

CARDS = [
    {"id": "CS2_001", "set": "VANILLA", "type": "SPELL", "collectible": True, "name": "A"},
    {"id": "CORE_001", "set": "CORE", "type": "MINION", "collectible": True, "name": "B"},
    {"id": "BG20_100", "set": "BATTLEGROUNDS", "type": "MINION", "name": "C"},
    {"id": "BG20_HERO_100", "set": "BATTLEGROUNDS", "type": "HERO", "name": "D"},
    {"id": "BG20_HERO_100p", "set": "BATTLEGROUNDS", "type": "HERO_POWER", "name": "E"},
    {"id": "BG20_HERO_100_Buddy", "set": "BATTLEGROUNDS", "type": "MINION",
     "isBattlegroundsBuddy": True, "name": "F"},
    {"id": "BG33_300", "set": "BATTLEGROUNDS", "type": "SPELL", "battlegroundsDarkmoonPrizeTurn": 0, "name": "G"},
    {"id": "BG24_Quest_1", "set": "BATTLEGROUNDS", "type": "SPELL", "mechanics": ["QUEST"], "name": "H"},
    {"id": "BG_Enchant", "set": "BATTLEGROUNDS", "type": "ENCHANTMENT", "name": ""},
    {"id": "LETL_001", "set": "LETTUCE", "type": "LETTUCE_ABILITY", "name": "I"},
    {"id": "X_merc", "set": "EXPERT1", "type": "MINION", "mercenariesRole": 1, "name": "J"},
    {"id": "TB_001", "set": "TB", "type": "SPELL", "name": "K"},
    {"id": "BOM_001", "set": "EXPERT1", "type": "SPELL", "name": "L"},
    {"id": "HERO_01", "set": "HERO_SKINS", "type": "HERO", "name": "M"},
    {"id": "PET_1", "set": "PET", "type": "PET", "name": "N"},
    {"id": "TOKEN_1", "set": "CORE", "type": "MINION", "name": "O"},
    {"id": "COIN_1", "set": "CORE", "type": "SPELL", "name": "P"},
    {"id": "CRED_1", "set": "CREDITS", "type": "MINION", "name": "Q"},
]
DB_SETS = {"standard": {"CORE_001"}, "wild": {"CORE_001", "CS2_001"},
           "coins": {"COIN_1"}, "bartenders": {"TOKEN_1"}}


def by_id(card_id):
    return next(c for c in CARDS if c["id"] == card_id)


def rows(locales, skip=()):
    return [{"card_id": c["id"], "locale": loc, "name": c["name"] or ""}
            for c in CARDS for loc in locales if (c["id"], loc) not in skip]


def cards_by_locale():
    return {loc: CARDS for loc in cov.LOCALES}


class ClassifyTest(unittest.TestCase):
    def test_groups(self):
        expect = {
            "CS2_001": {"classic", "wild"},
            "CORE_001": {"standard", "wild"},
            "BG20_100": {"bg_minions"},
            "BG20_HERO_100": {"bg_heroes"},
            "BG20_HERO_100p": {"bg_hero_powers"},
            "BG20_HERO_100_Buddy": {"bg_buddies"},
            "BG33_300": {"bg_darkmoon"},
            "BG24_Quest_1": {"bg_quests"},
            "BG_Enchant": {"bg_other"},
            "LETL_001": {"mercenaries"},
            "X_merc": {"mercenaries"},
            "TB_001": {"brawl"},
            "BOM_001": {"adventures"},
            "HERO_01": {"hero_skins"},
            "PET_1": {"pets"},
            "TOKEN_1": {"bartenders"},
            "COIN_1": {"coins"},
            "CRED_1": {"other"},
        }
        for cid, groups in expect.items():
            self.assertEqual(cov.classify(by_id(cid), DB_SETS), groups, cid)

    def test_remainder_goes_to_other_without_db(self):
        self.assertEqual(cov.classify({"id": "ZZ_1", "set": "CREDITS", "type": "MINION"}), {"other"})
        self.assertEqual(cov.classify({"id": "CORE_001", "set": "CORE"}), {"other"})

    def test_collectible_adventure_prefix_is_not_adventure(self):
        card = {"id": "BOM_x", "set": "EXPERT1", "collectible": True}
        self.assertNotIn("adventures", cov.classify(card))

    def test_every_id_has_a_group(self):
        report = cov.build_report(cards_by_locale(), [], DB_SETS)
        self.assertEqual(report["grouped_total"], report["hsj_total"], len(CARDS))
        for card in CARDS:
            self.assertTrue(cov.classify(card, DB_SETS))


class CoverageTest(unittest.TestCase):
    def group(self, report, key):
        return next(g for g in report["groups"] if g["key"] == key)

    def test_empty_table_is_zero(self):
        report = cov.build_report(cards_by_locale(), [], DB_SETS)
        cell = self.group(report, "bg_heroes")["locales"]["plPL"]
        self.assertEqual((cell["covered"], cell["percent"]), (0, 0.0))
        self.assertEqual(cell["missing_examples"], ["BG20_HERO_100"])

    def test_missing_plpl_row_lowers_only_plpl(self):
        data = rows(cov.LOCALES, skip={("BG20_HERO_100", "plPL")})
        report = cov.build_report(cards_by_locale(), data, DB_SETS)
        heroes = self.group(report, "bg_heroes")["locales"]
        self.assertEqual(heroes["plPL"]["percent"], 0.0)
        self.assertIn("BG20_HERO_100", heroes["plPL"]["missing_examples"])
        self.assertEqual(heroes["esES"]["percent"], 100.0)

    def test_empty_name_row_is_not_covered(self):
        data = rows(cov.LOCALES)
        for r in data:
            if r["card_id"] == "CS2_001" and r["locale"] == "esES":
                r["name"] = "  "
        report = cov.build_report(cards_by_locale(), data, DB_SETS)
        self.assertEqual(self.group(report, "classic")["locales"]["esES"]["missing_examples"], ["CS2_001"])

    def test_name_empty_in_hsj_is_not_a_gap(self):
        report = cov.build_report(cards_by_locale(), rows(cov.LOCALES), DB_SETS)
        other = self.group(report, "bg_other")["locales"]
        self.assertTrue(all(v["percent"] == 100.0 for v in other.values()))
        self.assertEqual(report["total"]["locales"]["ruRU"]["percent"], 100.0)

    def test_twist_and_arena_are_na(self):
        report = cov.build_report(cards_by_locale(), rows(cov.LOCALES), DB_SETS)
        for key in ("twist", "arena"):
            g = self.group(report, key)
            self.assertIn("na", g)
            self.assertEqual(g["locales"], {})
        md = cov.render_markdown(report)
        self.assertIn("| Твист | — | n/a | n/a | n/a | n/a |", md)

    def test_db_groups_are_na_without_db_sets(self):
        report = cov.build_report(cards_by_locale(), [], None)
        self.assertIn("na", self.group(report, "standard"))
        self.assertIn("na", self.group(report, "coins"))

    def test_examples_limit(self):
        report = cov.build_report(cards_by_locale(), [], DB_SETS, examples=1)
        self.assertEqual(len(self.group(report, "mercenaries")["locales"]["ruRU"]["missing_examples"]), 1)


class CliTest(unittest.TestCase):
    def test_rows_file_json_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            for loc in cov.LOCALES:
                (Path(tmp) / f"{loc}.json").write_text(json.dumps(CARDS))
            rows_file = Path(tmp) / "rows.json"
            rows_file.write_text(json.dumps(rows(cov.LOCALES)))
            sets_file = Path(tmp) / "sets.json"
            sets_file.write_text(json.dumps({k: sorted(v) for k, v in DB_SETS.items()}))
            out = []
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")), \
                    mock.patch("builtins.print", side_effect=lambda s: out.append(s)):
                code = cov.main(["--hsj-dir", tmp, "--rows-file", str(rows_file),
                                 "--db-sets-file", str(sets_file), "--json"])
            self.assertEqual(code, 0)
            report = json.loads(out[0])
            self.assertEqual(report["total"]["locales"]["plPL"]["percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
