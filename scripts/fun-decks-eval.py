#!/usr/bin/env python3
"""Read-only dry run of the fun-deck detector (concept-v8) on the live datasets.

Runs ``compute_fun_decks`` of THIS checkout against the datasets in
``HS_API_DATA_DIR`` and prints what the page would show, how that differs from the
published ``hsguru_fun_decks``, and how the detector does on the golden set
(``tests/fixtures/fun_decks_golden.json``). Nothing is written: every storage writer
is replaced by one that raises, and the data directory is meant to be mounted
read-only. Run it from the image of the service with this checkout mounted:

    docker run --rm --network none \\
      -v /srv/hs-data-api/data:/var/lib/hs-data-api:ro \\
      -v "$PWD":/src:ro -w /src -e PYTHONPATH=/src -e PYTHONDONTWRITEBYTECODE=1 \\
      hs-data-api:local python scripts/fun-decks-eval.py

``--dump-fixture`` prints, instead of the report, the trimmed real catalog slice
(``tests/fixtures/fun_decks_catalog_slice.json``) the regression test builds its index
from: the golden decks and the neighbours that decide their verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import fun_decks, storage
from app.config import data_dir
from app.fun_decks import (
    CatalogDeck,
    CatalogIndex,
    FunDeckScore,
    compute_fun_decks,
    deck_card_multiset,
    jaccard_similarity,
    score_fun_deck,
)

GOLDEN = ROOT / "tests" / "fixtures" / "fun_decks_golden.json"
LADDER_MEMBER_SIM = 0.55


def _refuse(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("fun-decks-eval is read-only: a storage writer was called")


def _make_read_only() -> None:
    for module, names in (
        (fun_decks, ("save_dataset", "save_status")),
        (
            storage,
            ("save_dataset", "save_status", "save_baseline", "save_baseline_once", "write_json"),
        ),
    ):
        for name in names:
            setattr(module, name, _refuse)


def _fun_decks_mtime() -> int | None:
    path = data_dir() / "datasets" / f"{fun_decks.SOURCE_ID}.json"
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _card_names() -> dict[int, str]:
    try:
        from app.cards_index import cards_by_dbfid

        return {int(k): str(v.get("name") or k) for k, v in cards_by_dbfid().items()}
    except Exception:  # noqa: BLE001 - names are decoration; dbfIds are enough
        return {}


def _composition(code: str, names: dict[int, str]) -> str:
    cards = deck_card_multiset(code) or {}
    parts = [
        f"{count}x {names.get(card, card)}" if count > 1 else str(names.get(card, card))
        for card, count in sorted(cards.items(), key=lambda kv: str(names.get(kv[0], kv[0])))
    ]
    return "; ".join(parts)


def _bucket(value: Any) -> str:
    return fun_decks._format_bucket(value)


def _score_golden(entry: dict[str, Any], index: CatalogIndex) -> FunDeckScore:
    return score_fun_deck(
        deck_code=entry["deck_code"],
        title=entry["title"],
        format_name=entry["format"],
        cards=deck_card_multiset(entry["deck_code"]),
        hero_class=entry["class"],
        win_rate=entry.get("win_rate"),
        catalog_games_hint=entry.get("games"),
        index=index,
    )


def _outcome(score: FunDeckScore) -> str:
    return (score.kind or "reject") if score.is_fun else "reject"


def _gates(score: FunDeckScore) -> str:
    return ",".join(r for r in score.reasons if r.endswith("_gate") or r == "below_min_score")


def _fmt_row(row: dict[str, Any]) -> str:
    return (
        f"{row.get('title')} [{row.get('class')}] kind={row.get('kind', 'brew')} "
        f"score={float(row.get('fun_score') or 0):.4f} ladder={row.get('ladder_sim')} "
        f"content={row.get('content_sim')} twin={row.get('twin_sim')} "
        f"games={row.get('games')} wr={row.get('win_rate')}"
    )


def _print_published(run: Any, names: dict[int, str]) -> None:
    for fmt in ("standard", "wild"):
        rows = sorted(
            (r for r in run.published if _bucket(r.get("format")) == fmt),
            key=lambda r: -float(r.get("fun_score") or 0),
        )
        kinds = Counter(str(r.get("kind") or "brew") for r in rows)
        print(f"\n## {fmt.capitalize()}: {len(rows)} decks ({dict(kinds)})")
        for index, row in enumerate(rows, 1):
            print(f"{index:2}. {_fmt_row(row)}")
            print(f"    reasons: {', '.join(row.get('reasons') or [])}")
            print(f"    nearest ladder: {row.get('nearest_archetype')}; content: {row.get('content_archetype')}")
            print(f"    cards: {_composition(row['deck_code'], names)}")


def _print_comparison(run: Any, previous: dict[str, Any]) -> None:
    data = previous.get("data") if isinstance(previous.get("data"), dict) else {}
    structured = data.get("structured") if isinstance(data, dict) else {}
    old_rows = [r for r in (structured or {}).get("rows") or [] if isinstance(r, dict)]
    old_version = (structured or {}).get("detector_version")
    key = lambda r: (_bucket(r.get("format")), str(r.get("deck_code")))
    old = {key(r): r for r in old_rows}
    new = {key(r): r for r in run.published}
    print(f"\n## Comparison with the published dataset ({old_version}, {len(old)} decks) -> concept-v8 ({len(new)} decks)")
    for label, keys in (
        ("Dropped (published now, not in v8)", old.keys() - new.keys()),
        ("Added (new in v8)", new.keys() - old.keys()),
        ("Kept", old.keys() & new.keys()),
    ):
        print(f"\n### {label}: {len(keys)}")
        for k in sorted(keys):
            if label.startswith("Dropped"):
                row = old[k]
                print(f"- {k[0]:8} {row.get('title')} [{row.get('class')}] v7 score={row.get('fun_score')}")
            elif label.startswith("Added"):
                print(f"- {k[0]:8} {_fmt_row(new[k])}")
            else:
                print(f"- {k[0]:8} {new[k].get('title')}: v7 {old[k].get('fun_score')} -> v8 {new[k].get('fun_score')} ({new[k].get('kind')})")


def _golden_report(run: Any, golden: dict[str, Any]) -> dict[str, Any]:
    published = {(_bucket(r.get("format")), r["deck_code"]) for r in run.published}
    rows = []
    for entry in golden["decks"]:
        score = _score_golden(entry, run.index)
        rows.append(
            {
                **entry,
                "outcome": _outcome(score),
                "score": score,
                "published": (_bucket(entry["format"]), entry["deck_code"]) in published,
            }
        )
    print(f"\n## Golden set ({len(rows)} decks, {golden.get('source', '')})")
    print("expected -> outcome | published | ladder/content/twin | score | gates | deck")
    for row in rows:
        s = row["score"]
        print(
            f"{row['expected']:9} -> {row['outcome']:8} | {'yes' if row['published'] else 'no ':3} | "
            f"{s.ladder_sim:.2f}/{s.content_sim:.2f}/{s.twin_sim:.2f} | {s.fun_score:.4f} | "
            f"{_gates(s) or '-':32} | {row['format'][:3]} {row['title']}"
        )
    rejects = [r for r in rows if r["expected"] == "reject"]
    brews = [r for r in rows if r["expected"] == "brew"]
    offs = [r for r in rows if r["expected"] == "off_meta"]
    passed = [r for r in rejects if r["outcome"] != "reject"]
    lost = [r for r in brews if r["outcome"] != "brew"]
    off_wrong = [r for r in offs if r["outcome"] == "brew"]
    print("\n### Metrics on the golden set")
    print(f"- reject: {len(rejects)}; passed as fun: {len(passed)} {[r['title'] for r in passed]}")
    print(f"- reject published on the live page: {[r['title'] for r in rejects if r['published']]}")
    print(f"- brew: {len(brews)}; lost: {len(lost)} {[(r['title'], _gates(r['score'])) for r in lost]}")
    print(f"- off_meta: {len(offs)}; outcomes {[(r['title'], r['outcome']) for r in offs]}; as brew (not allowed): {len(off_wrong)}")
    disputed = [r for r in rows if r["expected"] == "disputed"]
    print(f"- disputed (not counted): {[(r['title'], r['outcome']) for r in disputed]}")
    return {"passed": len(passed), "lost": len(lost), "off_wrong": len(off_wrong)}


def _acceptance(run: Any) -> None:
    print("\n## Acceptance checks on the published decks")
    over = [r["title"] for r in run.published if float(r.get("ladder_sim") or 0) >= LADDER_MEMBER_SIM]
    print(f"- decks with ladder_sim >= {LADDER_MEMBER_SIM}: {len(over)} {over}")
    for fmt in ("standard", "wild"):
        scores = [float(r.get("fun_score") or 0) for r in run.published if _bucket(r.get("format")) == fmt]
        if scores:
            top = max(scores)
            print(f"- {fmt}: max score {top:.4f}, decks at the max: {sum(1 for x in scores if x == top)}; score range {min(scores):.4f}..{top:.4f}")


def _run_report(args: argparse.Namespace) -> int:
    before = _fun_decks_mtime()
    names = _card_names()
    previous = storage.load_dataset(fun_decks.SOURCE_ID) or {}
    started = time.perf_counter()
    run = compute_fun_decks(args.format, [])
    elapsed = time.perf_counter() - started

    print(f"# fun-decks-eval: detector {fun_decks.DETECTOR_VERSION}, format_focus={run.focus}")
    print(
        f"catalog lists compared: {len(run.index.decks)}; ladder archetype names: {len(run.index.ladder_names)}; "
        f"candidates: {len(run.candidates)}; fresh fun: {len(run.fresh)}; published: {len(run.published)}"
    )
    print(f"rejected: {run.rejected}; by gate: {run.rejected_by_gate}; quality rejected: {run.quality_rejected}")
    print(f"near duplicates absorbed: {run.near_duplicates}; fresh by format: {run.format_counts}")
    kinds = Counter(str(r.get("kind") or "brew") for r in run.published)
    print(f"published by kind: {dict(kinds)}")
    _print_published(run, names)
    _print_comparison(run, previous)
    if GOLDEN.exists():
        _golden_report(run, json.loads(GOLDEN.read_text(encoding="utf-8")))
    _acceptance(run)
    after = _fun_decks_mtime()
    print(f"\n## Run time: {elapsed:.1f} s (index, candidates, scoring, selection)")
    print(f"## hsguru_fun_decks.json mtime_ns before={before} after={after} unchanged={before == after}")
    return 0


# --- regression slice ----------------------------------------------------------


def _slice(run: Any, golden: dict[str, Any], *, top: int = 5, top_ladder: int = 3, top_title: int = 3) -> dict[str, Any]:
    """The golden decks and the catalog lists that decide their verdict."""
    index: CatalogIndex = run.index
    chosen: dict[str, CatalogDeck] = {}
    by_code = {d.deck_code: d for d in index.decks}
    for entry in golden["decks"]:
        if entry["deck_code"] in by_code:
            chosen[entry["deck_code"]] = by_code[entry["deck_code"]]
        cards = deck_card_multiset(entry["deck_code"])
        fmt = _bucket(entry["format"])
        title_key = fun_decks.strip_format_prefix(entry["title"]).casefold()
        group = [
            (jaccard_similarity(cards, d.cards), d)
            for d in index.neighbours(fmt, entry["class"])
            if d.deck_code != entry["deck_code"]
        ]
        group.sort(key=lambda sd: -sd[0])
        ladder = [sd for sd in group if index.is_ladder_archetype(sd[1].format, sd[1].archetype)]
        titled = [sd for sd in group if sd[1].archetype and fun_decks._names_align(title_key, sd[1].archetype)]
        for _, deck in [*group[:top], *ladder[:top_ladder], *titled[:top_title]]:
            chosen[deck.deck_code] = deck
    decks = sorted(chosen.values(), key=lambda d: (d.format, d.hero_class, d.archetype, d.deck_code))
    archetype_games = {
        f"{fmt}|{name}": games
        for (fmt, name), games in index.archetype_games.items()
        if any(d.format == fmt and d.archetype == name for d in decks)
    }
    return {
        "generated_from": "scripts/fun-decks-eval.py --dump-fixture",
        "detector_version": fun_decks.DETECTOR_VERSION,
        "format_games": dict(index.format_games),
        "archetype_games": archetype_games,
        "decks": [
            {
                "deck_code": d.deck_code,
                "archetype": d.label,
                "class": d.hero_class,
                "format": d.format,
                "games": d.games,
            }
            for d in decks
        ],
    }


def _dump_fixture(args: argparse.Namespace) -> int:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    run = compute_fun_decks(args.format, [])
    payload = _slice(run, golden)
    # The slice must give every golden deck the verdict the whole catalog gives it.
    slice_index = CatalogIndex(
        [
            CatalogDeck(
                deck_code=d["deck_code"],
                cards=deck_card_multiset(d["deck_code"]) or fun_decks.Counter(),
                archetype=d["archetype"].casefold(),
                label=d["archetype"],
                hero_class=d["class"],
                format=d["format"],
                games=d["games"],
            )
            for d in payload["decks"]
        ],
        archetype_games={tuple(k.split("|", 1)): v for k, v in payload["archetype_games"].items()},  # type: ignore[misc]
        format_games=payload["format_games"],
    )
    mismatches = []
    for entry in golden["decks"]:
        full = _score_golden(entry, run.index)
        cut = _score_golden(entry, slice_index)
        if (_outcome(full), full.reasons) != (_outcome(cut), cut.reasons):
            mismatches.append(entry["title"])
    print(f"slice: {len(payload['decks'])} lists; verdict mismatches vs the full catalog: {mismatches}", file=sys.stderr)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 1 if mismatches else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--format", choices=["standard", "wild"], default=None, help="focus like a --format refresh (default: both formats)")
    parser.add_argument("--dump-fixture", action="store_true", help="print the trimmed real catalog slice instead of the report")
    args = parser.parse_args()
    _make_read_only()
    if os.environ.get("HS_API_DATA_DIR") is None:
        print(f"data dir: {data_dir()}", file=sys.stderr)
    return _dump_fixture(args) if args.dump_fixture else _run_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
