#!/usr/bin/env python3
"""Coverage report "locale x mode" for hearthstone_card_localizations.

Read-only. The reference is the HSJ id set (enUS cards.json). A card is covered
in a locale when the table has a row (card_id, locale) with a non-empty name and the 7
localizable payload fields equal HSJ of that locale. A field absent (or empty)
in HSJ is never a gap. Gaps are "missing" (no row / empty name) or "stale"
(a field differs).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

LOCALES = ("ruRU", "enUS", "plPL", "esES")
MAX_BYTES = 32 * 1024 * 1024
NA = "n/a"
LOCALIZED_FIELDS = ("name", "text", "flavor", "collectionText", "howToEarn",
                    "howToEarnGolden", "targetingArrowText")

ADVENTURE_SETS = {"MISSIONS", "TAVERNS_OF_TIME", "TUTORIAL"}
ADVENTURE_ID = re.compile(
    r"^(Story|BOM|BOH|BOTA|BTA|DALA|ULDA|GILA|LOOTA|TRLA|DRGA|ICCA\d+|LOEA\d+|"
    r"BRMA\d+|NAX\d+|KARA|Prologue|TUTR|TU\d+[a-z]?)_"
)

# (key, title). Order is the report order. Rows with a reason in NA_GROUPS are n/a.
GROUPS = (
    ("standard", "Стандарт"),
    ("wild", "Вольный"),
    ("classic", "Классика"),
    ("twist", "Твист"),
    ("arena", "Арена"),
    ("bg_minions", "Поля сражений: существа"),
    ("bg_heroes", "Поля сражений: герои"),
    ("bg_hero_powers", "Поля сражений: силы героев"),
    ("bg_spells", "Поля сражений: заклинания"),
    ("bg_trinkets", "Поля сражений: аксессуары"),
    ("bg_anomalies", "Поля сражений: аномалии"),
    ("bg_quests", "Поля сражений: квесты и награды"),
    ("bg_buddies", "Поля сражений: напарники"),
    ("bg_darkmoon", "Поля сражений: призы Ярмарки"),
    ("bg_other", "Поля сражений: эффекты и служебные"),
    ("mercenaries", "Наёмники"),
    ("brawl", "Потасовка"),
    ("adventures", "Приключения"),
    ("hero_skins", "Скины героев"),
    ("coins", "Монетки"),
    ("pets", "Питомцы"),
    ("bartenders", "Тавернщики"),
    ("other", "Прочие"),
)
TITLES = dict(GROUPS)
NA_REASONS = {
    "twist": "в HSJ и БД нет признака пула Твиста: подмножество коллекционных "
    "карт Вольного и Классики и их токенов",
    "arena": "в HSJ и БД нет признака пула Арены (ротация вне данных)",
}
# Groups that need ids from the DB; n/a when no DB sets were supplied.
DB_SET_GROUPS = {
    "standard": "standard",
    "wild": "wild",
    "coins": "coins",
    "bartenders": "bartenders",
}


# BG cards that keep a classic/event `set`: BG_OG_044_G, BGS_039, TB_BaconUps_*, BG31_BOBt*.
# HSJ has no BG flag for them (techLevel / battlegrounds*DbfId also appear on
# collectible constructed cards), so the id prefix is the reliable signal.
BG_ID = re.compile(r"^(BG\d*_|BGS_|TB_BaconUps_|HM_BG_)")


def classify(card: dict[str, Any], db_sets: dict[str, set[str]] | None = None) -> set[str]:
    """Return the group keys of one HSJ card. Never empty."""
    db_sets = db_sets or {}
    cid = card.get("id") or ""
    cset, ctype = card.get("set"), card.get("type")
    is_bg = cset == "BATTLEGROUNDS" or bool(BG_ID.match(cid))
    collectible = bool(card.get("collectible"))
    groups: set[str] = set()

    for key, set_name in DB_SET_GROUPS.items():
        if cid in db_sets.get(set_name, ()):
            groups.add(key)
    if cset == "VANILLA" and collectible:
        groups.add("classic")

    if is_bg and ctype == "HERO":
        groups.add("bg_heroes")
    if is_bg and ctype == "HERO_POWER":
        groups.add("bg_hero_powers")
    if is_bg and ctype == "MINION" and not card.get("isBattlegroundsBuddy"):
        groups.add("bg_minions")
    if card.get("isBattlegroundsBuddy") or ctype == "BATTLEGROUND_HERO_BUDDY":
        groups.add("bg_buddies")
    if ctype == "BATTLEGROUND_SPELL":
        groups.add("bg_spells")
    if ctype == "BATTLEGROUND_TRINKET":
        groups.add("bg_trinkets")
    if ctype == "BATTLEGROUND_ANOMALY":
        groups.add("bg_anomalies")
    if ctype == "BATTLEGROUND_QUEST_REWARD" or (
        is_bg and ctype == "SPELL" and "QUEST" in (card.get("mechanics") or [])
    ):
        groups.add("bg_quests")
    if card.get("battlegroundsDarkmoonPrizeTurn") is not None:
        groups.add("bg_darkmoon")
    bg_groups = {g for g in groups if g.startswith("bg_")}
    if is_bg and not bg_groups:
        groups.add("bg_other")

    if (
        cset == "LETTUCE"
        or ctype == "LETTUCE_ABILITY"
        or "mercenariesRole" in card
        or "mercenariesAbilityCooldown" in card
    ):
        groups.add("mercenaries")
    if cset == "TB":
        groups.add("brawl")
    if not collectible and (cset in ADVENTURE_SETS or ADVENTURE_ID.match(cid)):
        groups.add("adventures")
    if cset == "HERO_SKINS":
        groups.add("hero_skins")
    if ctype == "PET":
        groups.add("pets")

    return groups or {"other"}


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    return payload if isinstance(payload, dict) else {}


def cell_status(row: dict[str, Any] | None, ref: dict[str, Any]) -> str:
    """Return "ok", "missing" (no row / empty name) or "stale" (field differs)."""
    if row is None:
        # no row is a gap unless HSJ itself has nothing localizable for the card
        return "ok" if all(ref.get(f) in (None, "") for f in LOCALIZED_FIELDS) else "missing"
    hsj_has_text = any(ref.get(f) not in (None, "") for f in LOCALIZED_FIELDS)
    if not str(row.get("name") or "").strip() and hsj_has_text:
        return "missing"
    payload = _payload(row)
    for field in LOCALIZED_FIELDS:
        expected = ref.get(field)
        if expected in (None, ""):
            continue  # absent in HSJ: not a gap
        if payload.get(field) != expected:
            return "stale"
    return "ok"


def percent(covered: int, total: int) -> float:
    """Floor to 2 decimals: 100.0 only when covered == total."""
    return covered * 10000 // total / 100 if total else 100.0


def _cell(ids: list[str], status: Callable[[str], str], examples: int) -> dict[str, Any]:
    missing, stale = [], []
    for cid in ids:
        st = status(cid)
        if st == "missing":
            missing.append(cid)
        elif st == "stale":
            stale.append(cid)
    covered = len(ids) - len(missing) - len(stale)
    return {
        "covered": covered,
        "percent": percent(covered, len(ids)),
        "missing_count": len(missing),
        "missing_examples": missing[:examples],
        "stale_count": len(stale),
        "stale_examples": stale[:examples],
    }


def build_report(
    cards_by_locale: dict[str, list[dict[str, Any]]],
    rows: Iterable[dict[str, Any]],
    db_sets: dict[str, set[str]] | None = None,
    examples: int = 5,
) -> dict[str, Any]:
    reference = cards_by_locale["enUS"]
    ref_ids = [c["id"] for c in reference if isinstance(c.get("id"), str)]
    refs: dict[str, dict[str, dict[str, Any]]] = {}
    base = {c["id"]: c for c in reference if isinstance(c.get("id"), str)}
    for locale in LOCALES:
        refs[locale] = {c["id"]: c for c in (cards_by_locale.get(locale) or reference)
                        if isinstance(c.get("id"), str)}

    def status_for(locale: str, table_: dict) -> Callable[[str], str]:
        return lambda cid: cell_status(table_.get((cid, locale)), refs[locale].get(cid) or base[cid])
    table: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        table[(row.get("card_id"), row.get("locale"))] = row

    members: dict[str, list[str]] = {key: [] for key, _ in GROUPS}
    for card in reference:
        cid = card.get("id")
        if not isinstance(cid, str):
            continue
        for key in classify(card, db_sets):
            members[key].append(cid)

    report_groups = []
    for key, title in GROUPS:
        ids = members[key]
        entry: dict[str, Any] = {"key": key, "title": title, "total": len(ids), "locales": {}}
        reason = NA_REASONS.get(key)
        if reason is None and key in DB_SET_GROUPS and db_sets is None:
            reason = "id группы берутся из БД; не переданы (нужен --db или --db-sets-file)"
        if reason is not None:
            entry["na"] = reason
            report_groups.append(entry)
            continue
        for locale in LOCALES:
            entry["locales"][locale] = _cell(ids, status_for(locale, table), examples)
        report_groups.append(entry)
    total_cov = {}
    for locale in LOCALES:
        total_cov[locale] = _cell(ref_ids, status_for(locale, table), examples)
    union = {cid for ids in members.values() for cid in ids}
    return {
        "locales": list(LOCALES),
        "hsj_total": len(ref_ids),
        "grouped_total": len(union),
        "total": {"locales": total_cov},
        "groups": report_groups,
    }


def render_markdown(report: dict[str, Any]) -> str:
    head = "| Группа | Всего id | " + " | ".join(f"{loc}: покрыто / %" for loc in report["locales"]) + " | Не покрыто (примеры) |"
    lines = [head, "|" + " --- |" * (3 + len(report["locales"]))]

    def examples(locales: dict[str, Any]) -> str:
        parts = []
        for loc, v in locales.items():
            for label, key in (("нет строки", "missing_examples"), ("устарела", "stale_examples")):
                if v[key]:
                    parts.append(f"{loc} {label}: " + ", ".join(f"`{i}`" for i in v[key]))
        return "; ".join(parts) or "—"

    for g in report["groups"]:
        if "na" in g:
            total = "—" if g["key"] in NA_REASONS or not g["total"] else g["total"]
            na_cells = " | ".join([NA] * len(report["locales"]))
            lines.append(f"| {g['title']} | {total} | {na_cells} | {g['na']} |")
            continue
        cells = " | ".join(f"{g['locales'][l]['covered']} / {g['locales'][l]['percent']:.2f}%" for l in report["locales"])
        lines.append(f"| {g['title']} | {g['total']} | {cells} | {examples(g['locales'])} |")
    t = report["total"]["locales"]
    cells = " | ".join(f"{t[l]['covered']} / {t[l]['percent']:.2f}%" for l in report["locales"])
    lines.append(f"| **Все id HSJ** | {report['hsj_total']} | {cells} | {examples(t)} |")
    lines.append("")
    if any("na" in g and g["key"] in DB_SET_GROUPS for g in report["groups"]):
        lines.append("Без `--db` коллекционные карты Стандарта и Вольного попадают в «Прочие» "
                     "(и другие группы по своим полям), а Стандарт, Вольный, Монетки и Тавернщики "
                     "выводятся как n/a.")
        lines.append("")
    lines.append(f"Id в группах (объединение): {report['grouped_total']} из {report['hsj_total']}.")
    return "\n".join(lines)


def load_hsj(locale: str, hsj_dir: str | None, hsj_file: str | None = None) -> list[dict[str, Any]]:
    if hsj_file:
        content = Path(hsj_file).read_bytes()
    elif hsj_dir:
        path = Path(hsj_dir) / f"{locale}.json"
        if not path.exists():
            path = Path(hsj_dir) / locale / "cards.json"
        content = path.read_bytes()
    else:
        request = urllib.request.Request(
            f"https://api.hearthstonejson.com/v1/latest/{locale}/cards.json",
            headers={"User-Agent": "KolodaHearthstone-locale-coverage/1.0"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("Snapshot exceeds 32 MiB")
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("HSJ snapshot is not a list")
    return data


class CoverageError(RuntimeError):
    """A user-facing failure: printed without a traceback, exit code 2."""


def read_db(config_loader: Callable[[], dict[str, Any]] | None = None):
    """Read-only SELECTs. Returns (rows, db_sets)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from sync_constructed_related_cards import connect_db, load_php_config  # noqa: E402
    except ImportError as exc:
        raise CoverageError(f"DB driver is unavailable ({exc}); run with the panel venv "
                            "or use --rows-file") from exc
    try:
        conn = connect_db(load_php_config())
    except Exception as exc:
        raise CoverageError(f"cannot connect to the DB: {type(exc).__name__}: {exc}") from exc
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            try:
                cur.execute("SELECT card_id, locale, name, payload FROM hearthstone_card_localizations")
            except Exception as exc:
                if exc.args and exc.args[0] == 1146:
                    raise CoverageError("table hearthstone_card_localizations does not exist yet "
                                        "(created by api#95)") from exc
                raise CoverageError(f"query failed: {type(exc).__name__}: {exc}") from exc
            rows = list(cur.fetchall())
            sets = {}
            for key, sql in (
                ("standard", "SELECT card_id FROM constructed_format_cards WHERE format_slug='standard' AND in_format=1"),
                ("wild", "SELECT card_id FROM constructed_format_cards WHERE format_slug='wild' AND in_format=1"),
                ("coins", "SELECT card_id FROM hearthstone_coins"),
                ("bartenders", "SELECT card_id FROM battlegrounds_bartender_skins"),
            ):
                try:
                    cur.execute(sql)
                except Exception as exc:
                    raise CoverageError(f"query failed ({key}): {type(exc).__name__}: {exc}") from exc
                sets[key] = {r["card_id"] for r in cur.fetchall()}
    finally:
        conn.close()
    return rows, sets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hsj-dir", help="dir with <locale>.json (or <locale>/cards.json); default: download")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--rows-file", help="JSON dump of table rows [{card_id, locale, name, payload}]; no DB")
    src.add_argument("--db", action="store_true", help="read rows read-only from the production DB")
    for loc in LOCALES:
        ap.add_argument(f"--hsj-{loc}", metavar="FILE", help=f"HSJ cards.json for {loc} (overrides --hsj-dir)")
    ap.add_argument("--db-sets-file", help="JSON {standard,wild,coins,bartenders: [card ids]}")
    ap.add_argument("--examples", type=int, default=5, help="max uncovered ids per cell")
    ap.add_argument("--json", action="store_true", help="JSON output instead of Markdown")
    args = ap.parse_args(argv)

    rows: list[dict[str, Any]] = []
    db_sets: dict[str, set[str]] | None = None
    if args.db:
        try:
            rows, db_sets = read_db()
        except CoverageError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    elif args.rows_file:
        rows = json.loads(Path(args.rows_file).read_text("utf-8"))
    if args.db_sets_file:
        raw = json.loads(Path(args.db_sets_file).read_text("utf-8"))
        db_sets = {k: set(v) for k, v in raw.items()}
    cards = {loc: load_hsj(loc, args.hsj_dir, getattr(args, f"hsj_{loc}")) for loc in LOCALES}
    report = build_report(cards, rows, db_sets, args.examples)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
