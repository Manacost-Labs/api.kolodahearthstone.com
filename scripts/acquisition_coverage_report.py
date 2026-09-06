"""Print offline coverage JSON; never fetch, initialize a database or publish."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.acquisition_coverage import (
    ListingEvidence,
    coverage_report,
    source_view,
)
from app.sources import SOURCE_BY_ID, SOURCES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--source-id", choices=sorted(SOURCE_BY_ID))
    parser.add_argument("--patch-id")
    parser.add_argument(
        "--evidence",
        type=Path,
        help="Sanitized, trusted adapter capture JSON for one source",
    )
    args = parser.parse_args()
    if args.evidence and not args.source_id:
        parser.error("--evidence requires --source-id")
    sources = [SOURCE_BY_ID[args.source_id]] if args.source_id else SOURCES
    reports = []
    try:
        for source in sources:
            view = source_view(
                source, snapshot_id=args.snapshot_id, patch_id=args.patch_id
            )
            if args.evidence:
                with args.evidence.open("rb") as handle:
                    content = handle.read(4 * 1024 * 1024 + 1)
                if len(content) > 4 * 1024 * 1024:
                    raise ValueError("capture too large")
                raw = json.loads(content)
                if not isinstance(raw, dict) or not isinstance(
                    raw.get("entity_ids"), list
                ):
                    raise ValueError("invalid capture")
                states = raw.get("detail_states", {})
                if not isinstance(states, dict):
                    raise ValueError("invalid detail states")
                listing = ListingEvidence(
                    raw["scope_id"],
                    tuple(raw["entity_ids"]),
                    exhausted=raw.get("exhausted", False),
                    expected_count=raw.get("expected_count"),
                    view_confirmed=raw.get("view_confirmed", False),
                )
                reports.append(
                    coverage_report(view, listing=listing, detail_states=states)
                )
            else:
                reports.append(coverage_report(view))
    except (OSError, ValueError, TypeError, KeyError):
        parser.error("invalid capture evidence or view; no report emitted")
    print(json.dumps(reports, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
