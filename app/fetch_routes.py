from __future__ import annotations

from collections.abc import Iterable

from .config import (
    fetch_backends,
    fetch_direct_enabled,
    firecrawl_fallback_source_ids,
    firecrawl_primary_source_ids,
    hsguru_fetch_backends,
)
from .scrapers.proxy import source_can_use_flaresolverr_without_proxy
from .sources import Source

_PROXYLESS_API_SOURCE_IDS: frozenset[str] = frozenset(
    {
        "firestone_battlegrounds_comps",
        "firestone_battlegrounds_cards",
        "firestone_battlegrounds_spells",
        "firestone_arena_cards_normal",
        "firestone_arena_cards_underground",
        "firestone_arena_legendaries_underground",
        "firestone_arena_legendaries_normal",
        "vicious_syndicate_live_beta",
    }
)


def configured_browser_backend_names(
    source: Source,
    *,
    default_backends: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return the exact configured local backend set used for a source."""
    if source.site == "hsguru":
        configured = hsguru_fetch_backends()
    elif default_backends is not None:
        configured = default_backends
    else:
        configured = fetch_backends()

    ordered: list[str] = []
    for value in configured:
        name = value.strip().lower()
        if name and name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def source_has_cloud_html_route(source: Source) -> bool:
    configured = firecrawl_primary_source_ids() | firecrawl_fallback_source_ids()
    return source.id in configured


def source_has_proxyless_local_route(
    source: Source,
    *,
    default_backends: Iterable[str] | None = None,
) -> bool:
    backends = configured_browser_backend_names(
        source,
        default_backends=default_backends,
    )
    return (
        "flaresolverr" in backends
        and source_can_use_flaresolverr_without_proxy(source)
    )


def source_can_run_without_residential_proxy(
    source: Source,
    *,
    default_backends: Iterable[str] | None = None,
) -> bool:
    """Return whether a configured source route survives residential proxy loss."""
    return (
        fetch_direct_enabled()
        or source.id in _PROXYLESS_API_SOURCE_IDS
        or source_has_cloud_html_route(source)
        or source_has_proxyless_local_route(
            source,
            default_backends=default_backends,
        )
    )


def source_local_route_requires_flaresolverr(
    source: Source,
    *,
    default_backends: Iterable[str] | None = None,
) -> bool:
    backends = configured_browser_backend_names(
        source,
        default_backends=default_backends,
    )
    return backends == ("flaresolverr",)
