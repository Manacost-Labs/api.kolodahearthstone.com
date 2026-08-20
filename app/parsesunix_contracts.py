"""Source-owned response contracts consumed by the ParsesUnix bridge.

These contracts prove that the acquired document has the expected wire shape.
Dataset semantics, completeness, freshness and publication remain in the
existing application gates.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from web_scraper import ResponseContract

from .source_contracts import get_contract
from .sources import Source
from .trinket_slices import (
    LEGACY_DEFAULT_TRINKET_SOURCE_IDS,
    TRINKET_SLICE_SOURCE_IDS,
)

STRICT_HSREPLAY_TRINKET_SOURCE_IDS = frozenset(
    (*LEGACY_DEFAULT_TRINKET_SOURCE_IDS, *TRINKET_SLICE_SOURCE_IDS)
)


def page_response_contract(source: Source) -> ResponseContract:
    """Return the fail-closed HTML contract for a registered page source."""

    source_contract = get_contract(source.id)
    minimum = source_contract.min_html_bytes if source_contract else 2_000
    canary_by_family = {
        ("hsguru", "meta"): "/archetype/",
        ("hsguru", "matchups"): "matchup",
        ("hsguru", "streamer_decks"): "streamer",
        ("heartharena", "arena"): "tierlist",
        ("hearthstone-decks", "ranked"): "hearthstone-decks",
        ("metastats", "ranked"): "hearthstone",
        ("metastats", "matchups"): "matchup",
        ("vicious-syndicate", "matchups"): "vicious",
        ("vicious-syndicate", "meta"): "vicious",
    }
    canary = canary_by_family.get((source.site, source.category))
    if canary is None:
        host = urlsplit(source.fetch_url).hostname or ""
        if not host:
            raise ValueError(f"Source {source.id} has no response-contract host")
        canary = host.removeprefix("www.").split(".", 1)[0]
    return ResponseContract.html(
        canaries=(canary,),
        min_body_bytes=max(200, minimum),
    )


def hsreplay_json_response_contract(url: str) -> ResponseContract:
    """Return a schema proof for a known HSReplay JSON endpoint."""

    path = urlsplit(url).path.rstrip("/") + "/"
    if path == "/api/v1/battlegrounds/trinkets/":
        return ResponseContract.json(
            required_json_paths=(
                "0.trinket_dbf_id",
                "0.group",
                "0.pick_rate",
                "0.avg_final_placement",
            ),
            min_body_bytes=100,
        )
    if path.startswith(("/analytics/query/", "/api/v1/analytics/query/")):
        return ResponseContract.json(
            required_json_paths=("series.data",),
            min_body_bytes=100,
        )
    if path == "/api/v1/archetypes/":
        return ResponseContract.json(
            required_json_paths=("data.0.id",),
            min_body_bytes=100,
        )
    if path.startswith("/api/v1/"):
        return ResponseContract.json(
            required_json_paths=("data",),
            min_body_bytes=20,
        )
    raise ValueError(f"No HSReplay JSON response contract for path {path}")


def hsreplay_json_contract_for_source(
    source_id: str,
    url: str,
) -> ResponseContract | None:
    """Return the first bounded HSReplay API rollout slice, or no contract yet."""

    if source_id not in STRICT_HSREPLAY_TRINKET_SOURCE_IDS:
        return None
    return hsreplay_json_response_contract(url)
