"""One Balance Radar run: statistics + social collection + analysis + storage."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
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
    select_posts,
)
from .clients import GetXApiClient, RedditApisClient
from .http import SocialApiError
from .schema import SCHEMA_VERSION, ModelCandidate, RadarResult
from .social import SocialBundle, SocialPost, collect_social, match_mentions
from .stats import StatsBundle, build_stats

logger = logging.getLogger(__name__)

FORMATS = ("standard", "wild")
TOP_POSTS = 5


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
) -> dict[str, Any]:
    posts_by_id = {post.id: post for post in posts}
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
            )
            for item in items
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
    }


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
) -> RunSummary:
    now = now or datetime.now(UTC)
    started_at = now.astimezone(UTC).isoformat(timespec="seconds")
    summary = RunSummary(started_at=started_at)
    bundles = {
        name: build_stats(name, loader=loader, cards_en=cards_en) for name in formats
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
        posts = social.for_format(name)
        mentions = match_mentions(posts, stats.aliases)
        mentioned = {post.id for matches in mentions.values() for post in matches}
        shown = select_posts(posts, mentioned)
        previous = store.latest_ok(name)
        model_input = build_model_input(
            stats=stats,
            posts=shown,
            mentions=mentions,
            social_window={"from": social.window_from, "to": social.window_to},
            patch=patch,
            previous=_previous_digest(previous),
            generated_at=started_at,
        )
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
                allowed_posts={post.id for post in shown},
                model=requested,
                client=claude,
            )
            payload = assemble_result(
                run_id=run_id,
                stats=stats,
                analysis=analysis,
                social=social,
                mentions=mentions,
                posts=shown,
                patch=patch,
                history=store.flag_history(name),
                generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
                requested_model=requested,
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
