"""Export sanitized panel snapshots offline; never fetch or open a queue."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.acquisition_panel import MAX_REPORT_BYTES, build_panel_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--observations",
        type=Path,
        help="Explicit trusted adapter observations JSON array",
    )
    parser.add_argument(
        "--output", type=Path, help="Optional private artifact path; default stdout"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly replace an existing output artifact",
    )
    args = parser.parse_args()
    temporary = None
    try:
        observations = []
        if args.observations:
            if not args.observations.is_file() or args.observations.is_symlink():
                raise ValueError("invalid input")
            with args.observations.open("rb") as handle:
                raw = handle.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES:
                raise ValueError("input too large")
            observations = json.loads(raw)
        body = json.dumps(
            build_panel_snapshot(observations), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if len(body) > MAX_REPORT_BYTES:
            raise ValueError("output too large")
        if args.output:
            if args.output.is_symlink() or (args.output.exists() and not args.replace):
                raise ValueError("output exists")
            with tempfile.NamedTemporaryFile(
                dir=args.output.parent, prefix=".acquisition-", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            if args.replace:
                os.replace(temporary, args.output)
            else:
                os.link(temporary, args.output)  # No overwrite race on first export.
                temporary.unlink()
            temporary = None
        else:
            print(body.decode("utf-8"))
    except (OSError, ValueError, TypeError, KeyError):
        parser.error(
            "invalid observations or unavailable output; existing report preserved"
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
