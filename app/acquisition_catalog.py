"""Offline rollout inventory, not a live freshness or completeness certificate."""

from collections.abc import Iterable
from typing import Any

from .sources import SOURCES, Source


def acquisition_catalog(sources: Iterable[Source] = SOURCES) -> list[dict[str, Any]]:
    rows = []
    for source in sources:
        history = source.id == "hearthstone_decks" and source.kind == "scrape"
        rows.append(
            {
                "source_id": source.id,
                "site": source.site,
                "category": source.category,
                "kind": source.kind,
                "acquisition_mode": "dedicated_pipeline"
                if source.kind == "pipeline"
                else "source_specific",
                "fragment_requires_adapter": bool(source.fragment),
                "history_collector": "collect_wordpress_history" if history else None,
                "history_activation": "explicit_candidate_only" if history else None,
                "listing_preserved_beyond_detail_budget": source.id
                == "hsreplay_battlegrounds_comps",
                "full_traversal_verified": False,
                "verification_required": "source_specific_capture_and_replay",
            }
        )
    return rows
