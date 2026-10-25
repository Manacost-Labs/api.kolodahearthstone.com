"""One Balance Radar run: statistics + social collection + analysis + storage."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..storage import load_dataset
from . import store
from .analysis import (
    AnalysisError,
    AnalysisResult,
    analysis_model,
    analyze,
    build_model_input,
    developer_posts,
    select_posts,
)
from .backtest import accuracy_block, backtest_summary, load_backtest
from .clients import GetXApiClient, RedditApisClient
from .editor import edit_texts, jobs_for_payload
from .history import (
    ArchetypeStats,
    all_changes,
    calibrate,
    ensure_history,
    find_precedents,
    model_context,
    precedent_view,
    timing_outlook,
)
from .http import SocialApiError
from .localize import finalize, localize
from .schema import SCHEMA_VERSION, ModelCandidate, RadarResult
from .social import SocialBundle, SocialPost, collect_social, match_mentions
from .stats import StatsBundle, _english_cards, build_stats
from .translate import translate_posts

logger = logging.getLogger(__name__)

FORMATS = ("standard", "wild")
TOP_POSTS = 3
# Precedents in the prompt: the strongest-scored candidates only, two each
# (the response still gets up to three per candidate).
PRECEDENT_REFS = 80
PRECEDENTS_PER_REF = 2


@dataclass
class FormatOutcome:
    format: str
    status: str
    run_id: str
    nerfs: int = 0
    buffs: int = 0
    cost_usd: float | None = None
    error: str | None = None


@dataclass
class RunSummary:
    started_at: str
    outcomes: list[FormatOutcome] = field(default_factory=list)
    social_usage: dict[str, Any] = field(default_factory=dict)
    social_sources: list[dict[str, Any]] = field(default_factory=list)
    balances: dict[str, Any] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return any(
            source.get("state") not in {"ok", "disabled"}
            for source in self.social_sources
        )

    def exit_code(self) -> int:
        statuses = [outcome.status for outcome in self.outcomes]
        if not statuses or all(status == "failed" for status in statuses):
            return 1
        if "failed" in statuses or self.degraded:
            return 10
        return 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "startedAt": self.started_at,
            "formats": [outcome.__dict__ for outcome in self.outcomes],
            "socialUsage": self.social_usage,
            "socialSources": self.social_sources,
            "balances": self.balances,
            "exitCode": self.exit_code(),
        }


def _latest_patch() -> dict[str, Any] | None:
    try:
        from ..patches_db import list_patches

        patches = list_patches(limit=1).get("patches") or []
    except Exception as exc:  # noqa: BLE001 - the patch label is optional context
        logger.warning("patch lookup failed: %s", type(exc).__name__)
        return None
    if not patches:
        return None
    patch = patches[0]
    return {
        "version": patch.get("version"),
        "title": patch.get("official_title") or patch.get("wiki_title"),
        "titleRu": patch.get("title"),
        "url": patch.get("official_url") or patch.get("wiki_url"),
        "publishedAt": patch.get("official_published_at") or patch.get("published_at"),
    }


def _post_view(post: SocialPost, cited: set[str]) -> dict[str, Any]:
    excerpt = (post.text or post.title or "").replace("\n", " ").strip()
    return {
        "text": post.text or None,  # the full original; textRu is its translation
        "platform": post.platform,
        "kind": post.kind,
        "id": post.id,
        "url": post.url,
        "author": post.author,
        "score": post.score,
        "comments": post.comments,
        "title": post.title,
        "excerpt": excerpt[:280] + ("…" if len(excerpt) > 280 else ""),
        "community": post.community,
        "createdAt": post.created_at or None,
        "official": post.official,
        "authorRole": post.author_role,
        "citedByModel": post.id in cited,
    }


def assemble_candidate(
    item: ModelCandidate,
    *,
    side: str,
    entity: Mapping[str, Any],
    mentions: Sequence[SocialPost],
    posts_by_id: Mapping[str, SocialPost],
    history: Mapping[tuple[str, str], Mapping[str, Any]],
    stats_fetched_at: str | None,
    social_window: Mapping[str, Any],
    generated_at: str,
    changes: Sequence[Mapping[str, Any]] = (),
    series: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    cited_posts = [
        posts_by_id[pid] for pid in item.evidence_post_ids if pid in posts_by_id
    ]
    cited = {post.id for post in cited_posts}
    top = [*cited_posts, *(post for post in mentions if post.id not in cited)][
        :TOP_POSTS
    ]
    previous = history.get((side, item.ref)) or {}
    stats = {key: value for key, value in entity["stats"].items()}
    return {
        "ref": item.ref,
        "kind": entity["kind"],
        "id": entity["id"],
        "dbfId": entity.get("dbfId"),
        "name": entity["name"],
        "nameEn": entity.get("nameEn"),
        "class": entity.get("class"),
        "className": entity.get("className"),
        "imageUrl": entity.get("imageUrl"),
        "cost": entity.get("cost"),
        "rarity": entity.get("rarity"),
        "set": entity.get("set"),
        "confidence": item.confidence,
        "headline": item.headline,
        "verdict": item.verdict,
        "reasons": item.reasons,
        "suggestedChange": item.suggested_change,
        "stats": stats,
        "social": {
            "mentions": {
                "total": len(mentions),
                "reddit": sum(1 for post in mentions if post.platform == "reddit"),
                "x": sum(1 for post in mentions if post.platform == "x"),
            },
            "sentiment": item.sentiment,
            "stance": item.community_stance,
            "summary": item.social_summary,
            "topPosts": [_post_view(post, cited) for post in top],
        },
        "dates": {
            "firstFlaggedAt": previous.get("firstFlaggedAt") or generated_at,
            "previousConfidence": previous.get("previousConfidence"),
            "statsFetchedAt": stats_fetched_at,
            "socialWindowFrom": social_window.get("from"),
            "socialWindowTo": social_window.get("to"),
        },
        "precedents": candidate_precedents(item, entity, side, changes),
        "keyNumbers": list(item.key_numbers),
        "predictedChanges": [change.model_dump() for change in item.predicted_changes],
        "metaImpact": item.meta_impact or None,
        "quotes": [
            _quote_view(quote.text, posts_by_id[quote.post_id])
            for quote in item.quotes
            if quote.post_id in posts_by_id
        ],
        "developerSignal": item.developer_signal or None,
        "calibration": _side_calibration(entity, side),
        "trend": _trend(item.confidence, previous.get("previousConfidence")),
        "confidenceHistory": [
            *((series or {}).get((side, item.ref)) or []),
            {
                "runId": run_id,
                "generatedAt": generated_at,
                "confidence": item.confidence,
            },
        ][-30:],
    }


def _quote_view(text: str, post: SocialPost) -> dict[str, Any]:
    return {
        "text": text,
        "postId": post.id,
        "platform": post.platform,
        "kind": post.kind,
        "url": post.url,
        "author": post.author,
        "score": post.score,
        "createdAt": post.created_at or None,
        "official": post.official,
        "authorRole": post.author_role,
    }


def _side_calibration(entity: Mapping[str, Any], side: str) -> dict[str, Any] | None:
    calibration = entity.get("calibration") or {}
    item = calibration.get("nerf" if side == "nerfs" else "buff")
    if not item:
        return None
    return {**item, "features": calibration.get("features")}


def _trend(confidence: int, previous: Any) -> dict[str, Any]:
    if not isinstance(previous, int):
        return {"status": "new", "previousConfidence": None, "delta": None}
    delta = confidence - previous
    status = "up" if delta >= 5 else "down" if delta <= -5 else "stable"
    return {"status": status, "previousConfidence": previous, "delta": delta}


def attach_calibration(
    stats: StatsBundle, calibration: Mapping[str, Any] | None, p14: float | None
) -> None:
    """Historical hit rates for each entity, from features comparable to the history."""
    if not calibration:
        return
    for entity in stats.entities.values():
        values = entity.get("stats") or {}
        if entity["kind"] == "card" and values.get("core"):
            core = values["core"][0]
            features = {
                "winrate": core.get("winrate"),
                "popularity": core.get("popularity"),
                "source": f"HSReplay Legend core archetype {core['archetype']}",
            }
        elif entity["kind"] == "archetype" and values.get("legendWinrate") is not None:
            features = {
                "winrate": values.get("legendWinrate"),
                "popularity": values.get("legendPopularity"),
                "source": "HSGuru Legend",
            }
        else:
            features = {
                "winrate": values.get("winrate"),
                "popularity": values.get("popularity"),
                "source": f"{values.get('source')} {values.get('rank')} (approximate)",
            }
        result: dict[str, Any] = {"features": features}
        for side in ("nerf", "buff"):
            cell = calibrate(
                calibration, side, features["winrate"], features["popularity"]
            )
            if cell:
                if p14 is not None and cell.get("historicalRate") is not None:
                    cell["withPatchIn14Days"] = round(cell["historicalRate"] * p14, 3)
                result[side] = cell
        entity["calibration"] = result


def changes_since_previous(
    payload: Mapping[str, Any], previous: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """New and dropped candidates and the biggest confidence moves since the last good run."""
    old = (previous or {}).get("payload") or {}
    if not old:
        return None

    def index(source: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
        return {
            (side, c["ref"]): c
            for side in ("nerfs", "buffs")
            for c in source.get(side) or []
        }

    before, after = index(old), index(payload)

    def view(side: str, candidate: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
        return {
            "side": side,
            "ref": candidate["ref"],
            "kind": candidate.get("kind"),
            "name": candidate.get("name"),
            "confidence": candidate.get("confidence"),
            **extra,
        }

    moved = []
    for key, candidate in after.items():
        if key in before:
            delta = candidate["confidence"] - before[key]["confidence"]
            if delta:
                moved.append(
                    view(
                        key[0],
                        candidate,
                        previousConfidence=before[key]["confidence"],
                        delta=delta,
                    )
                )
    moved.sort(key=lambda item: item["delta"])
    return {
        "previousRunId": old.get("runId"),
        "previousGeneratedAt": old.get("generatedAt"),
        "added": [
            view(side, c)
            for (side, ref), c in after.items()
            if (side, ref) not in before
        ],
        "removed": [
            view(side, c)
            for (side, ref), c in before.items()
            if (side, ref) not in after
        ],
        "risen": [item for item in reversed(moved) if item["delta"] > 0][:10],
        "fallen": [item for item in moved if item["delta"] < 0][:10],
    }


def candidate_counts(payload: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for side in ("nerfs", "buffs"):
        kinds = Counter(c.get("kind") for c in payload.get(side) or [])
        out[side] = {
            "total": sum(kinds.values()),
            **{kind: kinds.get(kind, 0) for kind in ("card", "archetype", "class")},
        }
    return out


def _precedent_side(entity: Mapping[str, Any]) -> str:
    scores = entity.get("scores") or {}
    return (
        "nerfs" if (scores.get("nerf") or 0) >= (scores.get("buff") or 0) else "buffs"
    )


def precedent_digest(
    entities: Mapping[str, Mapping[str, Any]], changes: Sequence[Mapping[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Up to three past changes per candidate, compact for the prompt."""
    digest: dict[str, list[dict[str, Any]]] = {}
    ranked = sorted(
        entities.items(),
        key=lambda item: max((item[1].get("scores") or {}).values() or [0]),
        reverse=True,
    )[:PRECEDENT_REFS]
    for ref, entity in ranked:
        found = find_precedents(
            entity, _precedent_side(entity), changes, limit=PRECEDENTS_PER_REF
        )
        if not found:
            continue
        digest[ref] = [
            {
                "id": f"precedent:{change['id']}",
                "patch": change["patch"],
                "date": change.get("date"),
                "card": change.get("name") or change.get("nameEn"),
                "class": change.get("class"),
                "direction": change["direction"],
                "fields": [
                    [f["field"], f["before"], f["after"]]
                    for f in change.get("fields") or []
                ],
                "summary": change.get("summary"),
                "setAgeDays": change.get("setAgeDays"),
                "core": [
                    [
                        a["archetype"],
                        (a.get("before") or {}).get("winrate"),
                        (a.get("before") or {}).get("popularity"),
                        (a.get("after") or {}).get("winrate"),
                    ]
                    for a in (change.get("archetypes") or [])[:1]
                ],
                "similarity": reasons,
            }
            for change, reasons in found
        ]
    return digest


def candidate_precedents(
    item: ModelCandidate,
    entity: Mapping[str, Any],
    side: str,
    changes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Precedents the model cited first, then the closest matches, at most three."""
    by_id = {f"precedent:{change['id']}": change for change in changes}
    retrieved = find_precedents(entity, side, changes, limit=3)
    reasons_by_id = {
        f"precedent:{change['id']}": reasons for change, reasons in retrieved
    }
    views = [
        precedent_view(by_id[pid], reasons_by_id.get(pid, ["cited"]), cited=True)
        for pid in getattr(item, "precedent_ids", []) or []
        if pid in by_id
    ]
    seen = {view["id"] for view in views}
    for change, reasons in retrieved:
        if len(views) >= 3:
            break
        view = precedent_view(change, reasons)
        if view["id"] not in seen:
            views.append(view)
            seen.add(view["id"])
    return views[:3]


def history_block(
    history: Mapping[str, Any] | None, backtest: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    if not history:
        return None
    narrative = history.get("narrative") or {}
    patterns = history.get("patterns") or {}
    return {
        "generatedAt": history.get("generatedAt"),
        "coverage": history.get("coverage"),
        "balancePatches": patterns.get("balancePatches"),
        "summary": narrative.get("summary"),
        "patterns": narrative.get("patterns") or [],
        "historyUrl": "/v1/constructed/balance-radar/history",
        "patternsUrl": "/v1/constructed/balance-radar/patterns",
        "calibration": {
            "samples": (patterns.get("calibration") or {}).get("samples"),
            "patches": (patterns.get("calibration") or {}).get("patches"),
            "nerfOverall": ((patterns.get("calibration") or {}).get("nerf") or {}).get(
                "overall"
            ),
            "buffOverall": ((patterns.get("calibration") or {}).get("buff") or {}).get(
                "overall"
            ),
        },
        "backtest": backtest_summary(backtest),
    }


def _previous_digest(previous: Mapping[str, Any] | None) -> dict[str, Any] | None:
    payload = (previous or {}).get("payload") or {}
    if not payload:
        return None
    return {
        "generatedAt": payload.get("generatedAt"),
        **{
            side: [
                {
                    "ref": item.get("ref"),
                    "confidence": item.get("confidence"),
                    "headline": item.get("headline"),
                }
                for item in payload.get(side) or []
            ]
            for side in ("nerfs", "buffs")
        },
    }


def input_hash(model_input: Mapping[str, Any]) -> str:
    stable = {
        key: value
        for key, value in model_input.items()
        if key not in {"generatedAt", "previousRun"}
    }
    social = dict(stable.get("social") or {})
    social.pop("window", None)
    stable["social"] = social
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def assemble_result(
    *,
    run_id: str,
    stats: StatsBundle,
    analysis: AnalysisResult,
    social: SocialBundle,
    mentions: Mapping[str, list[SocialPost]],
    posts: Sequence[SocialPost],
    patch: Mapping[str, Any] | None,
    history: Mapping[tuple[str, str], Mapping[str, Any]],
    generated_at: str,
    requested_model: str,
    balance_history: Mapping[str, Any] | None = None,
    timing: Mapping[str, Any] | None = None,
    previous: Mapping[str, Any] | None = None,
    series: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]] | None = None,
    backtest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    posts_by_id = {post.id: post for post in posts}
    changes = all_changes(balance_history)
    window = {"from": social.window_from, "to": social.window_to}
    meta_fetched = next(
        (s.get("fetchedAt") for s in stats.sources if s["id"] == "hsguru_meta_matrix"),
        None,
    )
    card_fetched = max(
        (
            str(s.get("fetchedAt") or "")
            for s in stats.sources
            if s["id"].startswith("hsreplay")
        ),
        default=None,
    )

    def side(items: list[ModelCandidate], name: str) -> list[dict[str, Any]]:
        return [
            assemble_candidate(
                item,
                side=name,
                entity=stats.entities[item.ref],
                mentions=mentions.get(item.ref, []),
                posts_by_id=posts_by_id,
                history=history,
                stats_fetched_at=card_fetched
                if item.ref.startswith("card:")
                else meta_fetched,
                social_window=window,
                generated_at=generated_at,
                changes=changes,
                series=series,
                run_id=run_id,
            )
            for item in sorted(items, key=lambda candidate: -candidate.confidence)
        ]

    social_total = sum(
        float(usage.get("costUsd") or 0) for usage in social.usage.values()
    )
    analysis_cost = analysis.usage.get("costUsd")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "format": stats.format,
        "generatedAt": generated_at,
        "summary": analysis.output.summary,
        "nerfs": side(analysis.output.nerfs, "nerfs"),
        "buffs": side(analysis.output.buffs, "buffs"),
        "patch": dict(patch) if patch else None,
        "statsPeriods": stats.periods,
        "model": analysis.model,
        "requestedModel": requested_model,
        "sources": [
            *stats.sources,
            *({"kind": "social", **source} for source in social.sources),
        ],
        "socialWindow": {
            **window,
            "posts": sum(
                1 for post in social.for_format(stats.format) if post.kind != "comment"
            ),
            "comments": sum(
                1 for post in social.for_format(stats.format) if post.kind == "comment"
            ),
            "postsSentToModel": len(posts),
        },
        "usage": {
            "analysis": analysis.usage,
            "social": social.usage,
            "socialSharedAcrossFormats": True,
            "socialCostUsd": round(social_total, 4),
            "totalCostUsd": round(social_total + float(analysis_cost or 0), 4),
            "droppedModelItems": analysis.dropped,
        },
        "history": history_block(balance_history, backtest),
        "accuracy": accuracy_block(backtest),
        "timing": {**dict(timing), "outlook": analysis.output.timing_outlook or None}
        if timing
        else None,
    }


def polish(
    payload: dict[str, Any],
    glossary: Any,
    posts: Mapping[str, SocialPost],
    *,
    claude: Any = None,
    translate: bool = True,
    edit: bool = True,
) -> None:
    """Russian-only reader texts: glossary cleanup, translations, editor, markup."""
    finalize(payload, glossary)
    translations: dict[str, Any] = {}
    if translate:
        jobs = []
        for side in ("nerfs", "buffs"):
            for candidate in payload.get(side) or []:
                for view in (candidate.get("social") or {}).get("topPosts") or []:
                    post = posts.get(view["id"])
                    if post is not None:
                        jobs.append(
                            {"id": post.id, "title": post.title, "text": post.text}
                        )
                for quote in candidate.get("quotes") or []:
                    jobs.append(
                        {
                            "id": f"quote:{quote['postId']}:{quote['text'][:40]}",
                            "title": None,
                            "text": quote["text"],
                        }
                    )
        unique = {job["id"]: job for job in jobs}
        translations, translation_usage = translate_posts(
            unique.values(), glossary_lines=glossary.as_lines(), claude=claude
        )
        payload["usage"]["translation"] = translation_usage
    if edit:
        originals = {"summary": payload.get("summary")}
        results, report = edit_texts(
            jobs_for_payload(payload, glossary), run_id=payload["runId"]
        )
        payload["editor"] = report
        if results.get("summary") and results["summary"].applied:
            payload["summary"] = results["summary"].text
        payload["original"] = originals
        for side in ("nerfs", "buffs"):
            for candidate in payload.get(side) or []:
                result = results.get(f"{side}:{candidate['ref']}:verdict")
                if result is None:
                    continue
                candidate["editor"] = result.report()
                if result.applied:
                    candidate["original"] = {"verdict": candidate["verdict"]}
                    candidate["verdict"] = result.text
    finalize(payload, glossary, translations=translations)
    payload["usage"]["totalCostUsd"] = round(
        float(payload["usage"].get("totalCostUsd") or 0)
        + float((payload["usage"].get("translation") or {}).get("costUsd") or 0),
        4,
    )


def _local_stats() -> tuple[Any, Any]:
    """The parser database (mulligan tables) and HSReplay archetype tables, if present."""
    try:
        from ..db import get_db_connection

        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001 - optional context
        logger.warning("local statistics database unavailable: %s", type(exc).__name__)
        return None, None
    try:
        return conn, ArchetypeStats(conn)
    except Exception as exc:  # noqa: BLE001 - HSReplay archetype tables are optional
        logger.info("HSReplay archetype tables unavailable: %s", type(exc).__name__)
        return conn, None


def _clients() -> tuple[RedditApisClient | None, GetXApiClient | None]:
    reddit = x = None
    if os.environ.get("REDDITAPIS_API_KEY"):
        reddit = RedditApisClient(os.environ["REDDITAPIS_API_KEY"])
    if os.environ.get("GETXAPI_API_KEY"):
        x = GetXApiClient(os.environ["GETXAPI_API_KEY"])
    return reddit, x


def _balances(*clients: Any) -> dict[str, Any]:
    balances: dict[str, Any] = {}
    for client in clients:
        if client is None:
            continue
        try:
            account = client.account()
        except SocialApiError as exc:
            balances[client.provider] = {"error": str(exc)}
            continue
        value = account.get("balance_total", account.get("credits_remaining"))
        balances[client.provider] = {
            "balanceUsd": value,
            "totalRequests": account.get("total_requests"),
        }
    return balances


def run_balance_radar(
    formats: Sequence[str] = FORMATS,
    *,
    dry_run: bool = False,
    force: bool = False,
    collect: bool = True,
    reddit: RedditApisClient | None = None,
    x: GetXApiClient | None = None,
    claude: Any = None,
    loader: Callable[[str], Mapping[str, Any] | None] = load_dataset,
    cards_en: Mapping[str, Mapping[str, Any]] | None = None,
    patch: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    dry_run_dir: Any = None,
    balance_history: Mapping[str, Any] | None = None,
    refresh_history: bool = True,
    db: Any = None,
    archetype_stats: Any = None,
    use_local_db: bool = True,
    translate_names: bool = True,
    translate: bool = True,
    edit: bool = True,
) -> RunSummary:
    now = now or datetime.now(UTC)
    started_at = now.astimezone(UTC).isoformat(timespec="seconds")
    summary = RunSummary(started_at=started_at)
    if db is None and use_local_db:
        db, archetype_stats = _local_stats()
    bundles = {
        name: build_stats(
            name,
            loader=loader,
            cards_en=cards_en,
            db=db,
            archetype_stats=archetype_stats,
            today=now.date().isoformat(),
        )
        for name in formats
    }
    standard = bundles.get("standard") or next(iter(bundles.values()), None)
    entity_terms: list[str] = []
    if standard is not None:
        top = sorted(
            [*standard.archetypes, *standard.card_nerf_pool],
            key=lambda r: r["scores"]["nerf"],
            reverse=True,
        )[:8]
        entity_terms = [str(r.get("nameEn") or r["name"]) for r in top]
    if reddit is None and x is None and collect:
        reddit, x = _clients()
    social = collect_social(reddit=reddit, x=x, entity_terms=entity_terms, now=now)
    summary.social_usage = social.usage
    summary.social_sources = social.sources
    summary.balances = _balances(reddit, x)
    patch = patch if patch is not None else _latest_patch()
    if balance_history is None and refresh_history:
        try:
            balance_history = ensure_history(claude=claude)
        except Exception as exc:  # noqa: BLE001 - history only adds context
            logger.warning("balance history unavailable: %s", exc)
    history_context = model_context(balance_history)
    changes = all_changes(balance_history)
    timing = timing_outlook(balance_history, now.date())
    calibration = ((balance_history or {}).get("patterns") or {}).get("calibration")
    backtest = load_backtest()
    glossary = localize(
        list(bundles.values()),
        changes,
        cards_en=cards_en if cards_en is not None else _english_cards(),
        claude=claude,
        translate=translate_names and not dry_run,
    )
    requested = analysis_model()
    for name, stats in bundles.items():
        run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{name}-{uuid.uuid4().hex[:6]}"
        if stats.empty:
            store.save_run(
                run_id=run_id,
                format_name=name,
                status="failed",
                started_at=started_at,
                error="statistics datasets are unavailable",
            )
            summary.outcomes.append(
                FormatOutcome(name, "failed", run_id, error="no statistics")
            )
            continue
        attach_calibration(
            stats, calibration, (timing or {}).get("nextPatchWithin14Days")
        )
        posts = social.for_format(name)
        mentions = match_mentions(posts, stats.aliases)
        mentioned = {post.id for matches in mentions.values() for post in matches}
        shown = select_posts(posts, mentioned)
        developer = developer_posts(posts)
        visible = {post.id: post for post in (*shown, *developer)}
        previous = store.latest_ok(name)
        model_input = build_model_input(
            stats=stats,
            posts=shown,
            mentions=mentions,
            social_window={"from": social.window_from, "to": social.window_to},
            patch=patch,
            previous=_previous_digest(previous),
            generated_at=started_at,
            history=history_context,
            precedents=precedent_digest(stats.entities, changes) if changes else None,
            timing=timing,
            developer=developer,
        )
        model_input["glossary"] = glossary.as_lines()
        if model_input.get("history"):
            # Past narratives were written with English names; the model copies them.
            model_input["history"] = glossary.clean_tree(model_input["history"])
        digest = input_hash(model_input)
        if dry_run:
            if dry_run_dir is not None:
                path = os.path.join(
                    str(dry_run_dir), f"balance-radar-input-{name}.json"
                )
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(model_input, handle, ensure_ascii=False, indent=2)
            summary.outcomes.append(
                FormatOutcome(name, "skipped", run_id, error="dry run")
            )
            continue
        if not force and previous and previous.get("inputHash") == digest:
            store.save_run(
                run_id=run_id,
                format_name=name,
                status="skipped",
                started_at=started_at,
                input_hash=digest,
                error="inputs unchanged since the latest run",
            )
            summary.outcomes.append(
                FormatOutcome(name, "skipped", run_id, error="inputs unchanged")
            )
            continue
        try:
            analysis = analyze(
                model_input,
                allowed_refs=set(stats.entities),
                allowed_posts=set(visible),
                post_texts={pid: post.body for pid, post in visible.items()},
                allowed_precedents={f"precedent:{change['id']}" for change in changes},
                model=requested,
                client=claude,
            )
            payload = assemble_result(
                run_id=run_id,
                stats=stats,
                analysis=analysis,
                social=social,
                mentions=mentions,
                posts=list(visible.values()),
                patch=patch,
                history=store.flag_history(name),
                generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
                requested_model=requested,
                balance_history=balance_history,
                timing=timing,
                previous=previous,
                series=store.confidence_series(name),
                backtest=backtest,
            )
            payload["changesSincePrevious"] = changes_since_previous(payload, previous)
            payload["counts"] = candidate_counts(payload)
            polish(
                payload,
                glossary,
                visible,
                claude=claude,
                translate=translate,
                edit=edit,
            )
            RadarResult.model_validate(payload)
        except (AnalysisError, ValueError) as exc:
            logger.error("balance radar %s analysis failed: %s", name, exc)
            store.save_run(
                run_id=run_id,
                format_name=name,
                status="failed",
                started_at=started_at,
                input_hash=digest,
                error=str(exc),
                model=requested,
            )
            summary.outcomes.append(
                FormatOutcome(name, "failed", run_id, error=str(exc))
            )
            continue
        store.save_run(
            run_id=run_id,
            format_name=name,
            status="ok",
            started_at=started_at,
            payload=payload,
            usage=payload["usage"],
            input_hash=digest,
        )
        summary.outcomes.append(
            FormatOutcome(
                name,
                "ok",
                run_id,
                nerfs=len(payload["nerfs"]),
                buffs=len(payload["buffs"]),
                cost_usd=payload["usage"]["totalCostUsd"],
            )
        )
    for client in (reddit, x):
        if client is not None:
            client.close()
    return summary
