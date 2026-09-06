"""Scoped acquisition evidence, independent of publication and freshness gates."""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from .sources import SOURCES, Source

DETAIL_STATES = (
    "succeeded",
    "absent",
    "failed",
    "retry",
    "running",
    "pending",
    "not_requested",
)


def _identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


@dataclass(frozen=True)
class SourceView:
    source: Source
    snapshot_id: str
    patch_id: str | None = None

    def __post_init__(self) -> None:
        if not _identifier(self.source.id) or not _identifier(self.snapshot_id):
            raise ValueError("invalid acquisition view")
        if self.patch_id is not None and not _identifier(self.patch_id):
            raise ValueError("invalid patch identity")

    @property
    def scope_id(self) -> str:
        # Include the original URL: query repetition and fragment semantics must
        # not be lost. A changed patch/capture cannot reuse a previous queue.
        raw = json.dumps(
            [self.source.id, self.source.url, self.snapshot_id, self.patch_id]
        )
        return hashlib.sha256(raw.encode()).hexdigest()


def source_view(
    source: Source, *, snapshot_id: str, patch_id: str | None = None
) -> SourceView:
    return SourceView(source, snapshot_id, patch_id)


@dataclass(frozen=True)
class ListingEvidence:
    scope_id: str
    entity_ids: tuple[str, ...]
    exhausted: bool = False
    expected_count: int | None = None
    view_confirmed: bool = False


def coverage_report(
    view: SourceView,
    *,
    listing: ListingEvidence | None = None,
    detail_states: Mapping[str, str] | None = None,
) -> dict:
    """Caller adapters supply end/filter evidence; HTTP success is not evidence.

    This reports the specified captured view, never all upstream views or
    freshness. `absent` is allowed only with an adapter-confirmed reason.
    """
    parsed = urlsplit(view.source.url)
    report = {
        "source_id": view.source.id,
        "site": view.source.site,
        "kind": view.source.kind,
        "scope_id": view.scope_id,
        "snapshot_id": view.snapshot_id,
        "patch_id": view.patch_id,
        "query": parse_qs(parsed.query, keep_blank_values=True),
        "fragment": parsed.fragment,
        "status": "unknown",
        "found": None,
        "expected_count": None,
        "listing_percent": None,
        "listing_complete": False,
        "view_confirmed": False,
        "details": None,
        "unresolved_details": None,
    }
    states = dict(detail_states or {})
    if listing is None:
        if states:
            raise ValueError("detail evidence requires a listing")
        return report
    if (
        listing.scope_id != view.scope_id
        or type(listing.entity_ids) is not tuple
        or len(listing.entity_ids) > 100_000
        or any(not _identifier(item) for item in listing.entity_ids)
        or len(set(listing.entity_ids)) != len(listing.entity_ids)
        or type(listing.exhausted) is not bool
        or type(listing.view_confirmed) is not bool
    ):
        raise ValueError("invalid listing evidence")
    found = len(listing.entity_ids)
    total = listing.expected_count
    if total is not None and (type(total) is not int or total < found):
        raise ValueError("inconsistent expected count")
    if set(states) - set(listing.entity_ids) or any(
        state not in DETAIL_STATES for state in states.values()
    ):
        raise ValueError("invalid detail evidence")
    counts = Counter(states.get(item, "not_requested") for item in listing.entity_ids)
    unresolved = found - counts["succeeded"] - counts["absent"]
    listing_complete = listing.exhausted and (total is None or total == found)
    report.update(
        {
            "status": "complete_for_view"
            if listing_complete and listing.view_confirmed and unresolved == 0
            else "partial",
            "found": found,
            "expected_count": total,
            "listing_percent": round(100 * found / total, 2) if total else None,
            "listing_complete": listing_complete,
            "view_confirmed": listing.view_confirmed,
            "details": {state: counts[state] for state in DETAIL_STATES},
            "unresolved_details": unresolved,
        }
    )
    return report


def registry_coverage(*, snapshot_id: str) -> list[dict]:
    return [
        coverage_report(source_view(source, snapshot_id=snapshot_id))
        for source in SOURCES
    ]
