from __future__ import annotations

from collections.abc import Iterable

from .config import (
    fetch_backends,
    fetch_direct_enabled,
    firecrawl_fallback_source_ids,
    firecrawl_primary_source_ids,
    hsguru_fetch_backends,
    hsreplay_json_channels,
    hsreplay_scrape_do_max_credits,
    hsreplay_scrape_do_max_requests,
    scrape_do_token,
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

# These adapters own an internal validated provider cascade (Scrape.do ->
# Firecrawl -> Bright Data -> Scrapfly). Their cloud path is available even
# when they are not listed in the generic Firecrawl primary/fallback env sets.
_BUILTIN_CLOUD_PROVIDER_SOURCE_IDS: frozenset[str] = frozenset(
    {
        "heartharena_tierlist",
        "metastats_decks",
        "metastats_matchups",
    }
)

# These registered sources reach ``fetch_hsreplay_json`` even though their
# legacy contract names still mention Firecrawl. Other JSON-backed HSReplay
# sources are identified by their explicit preferred-channel contract below.
_HSREPLAY_JSON_SOURCE_IDS_WITHOUT_CHANNEL_CONTRACT: frozenset[str] = frozenset(
    {
        "hsreplay_arena_class_pages_firecrawl",
        "hsreplay_meta_top_1000_legend_1d_firecrawl",
        "hsreplay_meta_legend_1d_firecrawl",
        "hsreplay_meta_diamond_4to1_1d_firecrawl",
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
    if source.id in configured:
        return True
    if source.id not in _BUILTIN_CLOUD_PROVIDER_SOURCE_IDS:
        return False
    if scrape_do_token():
        return True

    # The built-in adapters call the same provider cascade as Firecrawl-backed
    # routes, but do not need to be duplicated in the generic source allowlist.
    from .brightdata_backend import brightdata_configured_for_source
    from .config import firecrawl_api_key
    from .scrapfly_backend import scrapfly_configured

    return bool(
        firecrawl_api_key()
        or brightdata_configured_for_source(source.id)
        or scrapfly_configured()
    )


def source_has_proxyless_local_route(
    source: Source,
    *,
    default_backends: Iterable[str] | None = None,
) -> bool:
    backends = configured_browser_backend_names(
        source,
        default_backends=default_backends,
    )
    return "flaresolverr" in backends and source_can_use_flaresolverr_without_proxy(
        source
    )


def source_has_hsreplay_scrape_do_json_route(source: Source) -> bool:
    """True only for registered HSReplay JSON jobs with usable Scrape.do."""

    if source.site != "hsreplay":
        return False
    if not scrape_do_token() or "scrape_do" not in hsreplay_json_channels():
        return False
    if hsreplay_scrape_do_max_requests() <= 0 or hsreplay_scrape_do_max_credits() <= 0:
        return False
    from .source_contracts import preferred_channels_for_source

    return bool(preferred_channels_for_source(source.id)) or (
        source.id in _HSREPLAY_JSON_SOURCE_IDS_WITHOUT_CHANNEL_CONTRACT
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
        or source_has_hsreplay_scrape_do_json_route(source)
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
