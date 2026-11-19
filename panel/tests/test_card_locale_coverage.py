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
    {"id": "CS2_001", "set": "VANILLA", "type": "SPELL", "collectible": True, "name": "A",
     "text": "Deal 1", "flavor": "Flavor"},
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
    {"id": "BG_OG_044_G", "set": "OG", "type": "MINION", "name": "R", "techLevel": 3},
    {"id": "TB_BaconUps_004", "set": "EXPERT1", "type": "MINION", "name": "S"},
    {"id": "BGS_039", "set": "LEGACY", "type": "SPELL", "name": "T"},
    {"id": "EMPTY_1", "set": "CREDITS", "type": "ENCHANTMENT", "name": ""},
    {"id": "CRED_1", "set": "CREDITS", "type": "MINION", "name": "Q"},
]
DB_SETS = {"standard": {"CORE_001"}, "wild": {"CORE_001", "CS2_001"},
           "coins": {"COIN_1"}, "bartenders": {"TOKEN_1"}}


def by_id(card_id):
    return next(c for c in CARDS if c["id"] == card_id)


def rows(locales, skip=()):
    return [{"card_id": c["id"], "locale": loc, "name": c["name"] or "",
             "payload": {k: c[k] for k in cov.LOCALIZED_FIELDS if c.get(k)}}
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
            "BG_OG_044_G": {"bg_minions"},
            "TB_BaconUps_004": {"bg_minions"},
            "BGS_039": {"bg_other"},
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


class EmptyNameTest(unittest.TestCase):
    def test_no_row_and_nothing_localizable_in_hsj_is_not_a_gap(self):
        data = rows(cov.LOCALES, skip={("EMPTY_1", loc) for loc in cov.LOCALES})
        report = cov.build_report(cards_by_locale(), data, DB_SETS)
        self.assertEqual(report["total"]["locales"]["plPL"]["percent"], 100.0)

    def test_no_row_with_hsj_text_is_a_gap(self):
        self.assertEqual(cov.cell_status(None, {"name": "", "text": "x"}), "missing")
        self.assertEqual(cov.cell_status(None, {"name": ""}), "ok")


class RoundingAndEmptyNameTest(unittest.TestCase):
    def test_percent_never_rounds_up_to_100(self):
        self.assertEqual(cov.percent(36389, 36390), 99.99)
        self.assertEqual(cov.percent(36390, 36390), 100.0)
        self.assertEqual(cov.percent(0, 5), 0.0)
        self.assertEqual(f"{cov.percent(36389, 36390):.2f}", "99.99")

    def test_report_99_99_for_one_gap(self):
        cards = [{"id": f"C{i}", "set": "CORE", "name": "n", "text": "t"} for i in range(36390)]
        by_loc = {loc: cards for loc in cov.LOCALES}
        data = [{"card_id": c["id"], "locale": loc, "name": "n", "payload": {"name": "n", "text": "t"}}
                for c in cards for loc in cov.LOCALES if (c["id"], loc) != ("C0", "plPL")]
        total = cov.build_report(by_loc, data)["total"]["locales"]
        self.assertEqual(total["plPL"]["percent"], 99.99)
        self.assertEqual(total["esES"]["percent"], 100.0)

    def test_empty_name_row_with_hsj_text_is_missing(self):
        row = {"card_id": "X", "locale": "esES", "name": "", "payload": {"text": "t"}}
        self.assertEqual(cov.cell_status(row, {"name": "", "text": "t"}), "missing")

    def test_empty_name_row_when_hsj_all_empty_is_ok(self):
        row = {"card_id": "X", "locale": "esES", "name": "", "payload": {}}
        self.assertEqual(cov.cell_status(row, {"name": ""}), "ok")


class FakeCursor:
    def __init__(self, fail_on=None):
        self.sql, self.fail_on = [], fail_on

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.sql.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(1146, "Table doesn't exist")

    def fetchall(self):
        return [{"card_id": "X", "locale": "plPL", "name": "n", "payload": "{}"}]


class FakeConn:
    def __init__(self, cursor):
        self.cur, self.closed = cursor, False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def fake_module(conn):
    import types
    return types.SimpleNamespace(connect_db=lambda cfg: conn, load_php_config=lambda: {})


class ReadDbTest(unittest.TestCase):
    def test_sets_read_only_first_and_reads_payload(self):
        cur = FakeCursor()
        conn = FakeConn(cur)
        with mock.patch.dict(sys.modules, {"sync_constructed_related_cards": fake_module(conn)}):
            rows_, sets = cov.read_db()
        self.assertEqual(cur.sql[0], "SET SESSION TRANSACTION READ ONLY")
        self.assertIn("payload", cur.sql[1])
        self.assertTrue(all(q.lstrip().upper().startswith(("SELECT", "SET SESSION")) for q in cur.sql))
        self.assertTrue(conn.closed)
        self.assertEqual(len(rows_), 1)
        self.assertEqual(set(sets), {"standard", "wild", "coins", "bartenders"})

    def run_main(self):
        err = []
        with mock.patch("sys.stderr") as stderr:
            stderr.write.side_effect = err.append
            code = cov.main(["--db", "--hsj-dir", "/nonexistent"])
        return code, "".join(err)

    def test_missing_table_is_a_clear_error(self):
        conn = FakeConn(FakeCursor(fail_on="hearthstone_card_localizations"))
        with mock.patch.dict(sys.modules, {"sync_constructed_related_cards": fake_module(conn)}):
            code, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)
        self.assertTrue(conn.closed)

    def test_missing_driver_is_a_clear_error(self):
        with mock.patch.dict(sys.modules, {"sync_constructed_related_cards": None}):
            code, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("driver is unavailable", err)


class StaleTest(unittest.TestCase):
    def cell(self, data, key="classic", locale="esES"):
        report = cov.build_report(cards_by_locale(), data, DB_SETS)
        return next(g for g in report["groups"] if g["key"] == key)["locales"][locale]

    def test_differing_text_is_stale_not_covered(self):
        data = rows(cov.LOCALES)
        for r in data:
            if r["card_id"] == "CS2_001" and r["locale"] == "esES":
                r["payload"]["text"] = "old text"
        cell = self.cell(data)
        self.assertEqual(cell["percent"], 0.0)
        self.assertEqual(cell["stale_examples"], ["CS2_001"])
        self.assertEqual(cell["missing_examples"], [])
        self.assertEqual(self.cell(data, locale="plPL")["percent"], 100.0)

    def test_missing_row_is_missing_not_stale(self):
        cell = self.cell(rows(cov.LOCALES, skip={("CS2_001", "plPL")}), locale="plPL")
        self.assertLess(cell["percent"], 100.0)
        self.assertEqual(cell["missing_examples"], ["CS2_001"])
        self.assertEqual(cell["stale_examples"], [])

    def test_field_absent_in_hsj_is_not_a_gap(self):
        data = rows(cov.LOCALES)
        for r in data:
            r["payload"].pop("flavor", None)  # row lacks a field...
        cards = {loc: [dict(c) for c in CARDS] for loc in cov.LOCALES}
        for c in cards["esES"]:
            c.pop("flavor", None)  # ...that HSJ esES does not have either
        report = cov.build_report(cards, data, DB_SETS)
        g = next(g for g in report["groups"] if g["key"] == "classic")
        self.assertEqual(g["locales"]["esES"]["percent"], 100.0)
        self.assertEqual(g["locales"]["ruRU"]["stale_examples"], ["CS2_001"])

    def test_reference_is_per_locale(self):
        cards = {loc: [dict(c) for c in CARDS] for loc in cov.LOCALES}
        for c in cards["plPL"]:
            if c["id"] == "CS2_001":
                c["text"] = "Zadaj 1"
        cell = next(g for g in cov.build_report(cards, rows(cov.LOCALES), DB_SETS)["groups"]
                    if g["key"] == "classic")["locales"]
        self.assertEqual(cell["plPL"]["stale_examples"], ["CS2_001"])
        self.assertEqual(cell["esES"]["percent"], 100.0)

    def test_payload_as_json_string(self):
        data = rows(cov.LOCALES)
        for r in data:
            r["payload"] = json.dumps(r["payload"])
        self.assertEqual(self.cell(data)["percent"], 100.0)


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
