from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .hsguru_decks import (
    HSGuruCatalogPartial,
    current_hsguru_deck_period,
    refresh_hsguru_deck_catalog,
    reset_hsguru_catalog_provider_state,
)
from .hsguru_meta_matrix import refresh_current_catalog_deck_join

CatalogRefresh = Callable[..., Awaitable[list[dict[str, Any]]]]
CatalogJoin = Callable[[], dict[str, Any]]


async def refresh_all_deck_catalogs(
    *,
    refresh: CatalogRefresh = refresh_hsguru_deck_catalog,
    join: CatalogJoin = refresh_current_catalog_deck_join,
) -> dict[str, Any]:
    """Refresh every HSGuru catalog even when an independent slice fails.

    Each catalog is persisted by ``refresh`` as soon as it succeeds. A single
    upstream failure is therefore reported without preventing the other
    format/rank combinations from becoming fresh.
    """
    reset_hsguru_catalog_provider_state()
    run_period = current_hsguru_deck_period()
    datasets: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    specs = (
        ("standard", "legend"),
        ("wild", "legend"),
        ("standard", "all"),
        ("wild", "all"),
    )
    for format_name, rank in specs:
        key = f"{format_name}_{rank}"
        try:
            rows = await refresh(format_name, rank, period=run_period)
        except HSGuruCatalogPartial as exc:
            datasets[key] = {
                "decks": len(exc.rows),
                "state": "partial",
                "missing_archetypes": len(exc.missing_archetypes),
                "zero_sample_archetypes": len(exc.zero_sample_archetypes),
            }
            errors[key] = f"{type(exc).__name__}: {str(exc)[:500]}"
            continue
        except Exception as exc:
            errors[key] = f"{type(exc).__name__}: {str(exc)[:500]}"
            continue
        datasets[key] = {"decks": len(rows), "state": "ok"}

    archetype_join: dict[str, Any] | None = None
    current_period = current_hsguru_deck_period()
    if current_period != run_period:
        errors["period"] = (
            "HSGuru current period changed during catalog refresh: "
            f"{run_period} -> {current_period}"
        )
    elif {"standard_all", "wild_all"}.issubset(datasets):
        try:
            archetype_join = join()
        except Exception as exc:
            errors["archetype_join"] = f"{type(exc).__name__}: {str(exc)[:500]}"

    return {
        "state": "ok" if not errors else "partial",
        "datasets": datasets,
        "errors": errors,
        "archetype_join": archetype_join,
        "period": run_period,
        **{f"{key}_decks": int(value["decks"]) for key, value in datasets.items()},
    }
