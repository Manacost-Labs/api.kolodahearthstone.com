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
from .storage import load_dataset

CatalogRefresh = Callable[..., Awaitable[list[dict[str, Any]]]]
CatalogJoin = Callable[[], dict[str, Any]]
CatalogDatasetLoader = Callable[[str], dict[str, Any] | None]


def _error_summary(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:500]}"


def _partial_dataset(exc: HSGuruCatalogPartial) -> dict[str, Any]:
    return {
        "decks": len(exc.rows),
        "state": "partial",
        "missing_archetypes": len(exc.missing_archetypes),
        "zero_sample_archetypes": len(exc.zero_sample_archetypes),
    }


def _persisted_all_rank_summary(
    format_name: str,
    period: str,
    *,
    expected_decks: int,
    load_persisted: CatalogDatasetLoader,
) -> dict[str, Any]:
    unavailable = {
        "missing_archetypes": None,
        "zero_sample_archetypes": None,
        "sample_state": None,
    }
    source_id = f"hsguru_deck_catalog_{format_name}_all"
    try:
        payload = load_persisted(source_id)
    except (OSError, TypeError, ValueError):
        return unavailable
    if not isinstance(payload, dict):
        return unavailable

    criteria = payload.get("criteria")
    rows = payload.get("data")
    missing = payload.get("missing_archetypes")
    zero_sample = payload.get("zero_sample_archetypes")
    sample_state = payload.get("sample_state")
    if (
        payload.get("source_id") != source_id
        or payload.get("state") != "ok"
        or payload.get("period") != period
        or not isinstance(criteria, dict)
        or criteria.get("format") != format_name
        or criteria.get("rank") != "all"
        or criteria.get("period") != period
        or not isinstance(rows, list)
        or len(rows) != expected_decks
        or not isinstance(missing, list)
        or bool(missing)
        or not isinstance(zero_sample, list)
        or not isinstance(sample_state, str)
        or not sample_state.strip()
    ):
        return unavailable
    return {
        "missing_archetypes": 0,
        "zero_sample_archetypes": len(zero_sample),
        "sample_state": sample_state,
    }


async def refresh_all_deck_catalogs(
    *,
    refresh: CatalogRefresh = refresh_hsguru_deck_catalog,
    join: CatalogJoin = refresh_current_catalog_deck_join,
    load_persisted: CatalogDatasetLoader = load_dataset,
) -> dict[str, Any]:
    """Refresh every HSGuru catalog even when an independent slice fails.

    Each catalog is persisted by ``refresh`` as soon as it succeeds. A partial
    catalog receives one immediate continuation so its bounded retry queue can
    advance without rerunning already successful format/rank combinations.
    Other failures are reported without preventing independent combinations
    from becoming fresh.
    """
    reset_hsguru_catalog_provider_state()
    run_period = current_hsguru_deck_period()
    datasets: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    continuations: dict[str, dict[str, Any]] = {}
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
            initial_error = _error_summary(exc)
            continuations[key] = {
                "state": "pending",
                "initial_error": initial_error,
            }
            try:
                rows = await refresh(format_name, rank, period=run_period)
            except HSGuruCatalogPartial as continuation_exc:
                final_error = _error_summary(continuation_exc)
                datasets[key] = _partial_dataset(continuation_exc)
                errors[key] = final_error
                continuations[key].update(
                    state="partial",
                    final_error=final_error,
                )
                continue
            except Exception as continuation_exc:
                final_error = _error_summary(continuation_exc)
                datasets[key] = _partial_dataset(exc)
                errors[key] = final_error
                continuations[key].update(
                    state="error",
                    final_error=final_error,
                )
                continue
            continuations[key]["state"] = "ok"
        except Exception as exc:
            errors[key] = _error_summary(exc)
            continue
        summary: dict[str, Any] = {"decks": len(rows), "state": "ok"}
        if rank == "all":
            summary.update(
                _persisted_all_rank_summary(
                    format_name,
                    run_period,
                    expected_decks=len(rows),
                    load_persisted=load_persisted,
                )
            )
        datasets[key] = summary

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
        "continuations": continuations,
        "archetype_join": archetype_join,
        "period": run_period,
        **{f"{key}_decks": int(value["decks"]) for key, value in datasets.items()},
    }
