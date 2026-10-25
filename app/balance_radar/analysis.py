"""Claude analysis of one format: statistics plus social digest in, JSON out."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from .. import provider_usage
from .schema import ModelOutput
from .social import SocialPost
from .stats import StatsBundle

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 64_000
MAX_CANDIDATES = 10
MAX_POSTS = 80
MAX_COMMENTS = 40
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# USD per million tokens: input, output, cache read, cache write (5 min).
PRICES_PER_MTOK: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.50),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.00),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.50, 6.25),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

SYSTEM_PROMPT = """Ты — аналитик баланса Hearthstone для русскоязычного сайта HearthPulse.
Твоя задача: по статистике и обсуждениям в соцсетях определить, какие карты,
архетипы и классы вероятнее всего понерфят или забаффают в ближайшем балансном
патче, и объяснить почему.

Как читать данные:
- Архетипы и классы — HSGuru, ранг all (и Legend отдельно). winrate и
  popularity в процентах; popularity — доля игр архетипа. Период statsPeriods.primary
  обычно HSGuru-окно «Bans» (*_ban): игры после последнего балансного/бан-обновления,
  это самое свежее состояние баланса. trend.winrateDelta/popularityDelta —
  past_day минус past_week; banWinrateDelta — окно банов минус всё дополнение.
- Карты — HSReplay. winrate — винрейт колод с картой, popularity — доля колод с
  картой. playedWinrate («винрейт при розыгрыше») систематически завышен у дорогих
  карт, используй его только как вспомогательный сигнал.
- scores.nerf/scores.buff — грубая эвристика ранжирования пула, не истина.
- social.posts — посты Reddit и X за окно socialWindow; mentions — сколько постов
  упоминают сущность. Сарказм и мемы — слабый сигнал; официальные аккаунты
  (official=true) и подробные разборы — сильный.
- previousRun — прошлый прогон радара: учитывай устойчивость сигналов.
- history.patterns — как Blizzard меняла баланс раньше: пороги винрейта и
  популярности перед нерфами и баффами, типичные правки, частота и сроки
  балансных патчей после выхода набора, эффект правок.
- history.precedents[ref] — похожие прошлые правки для каждого кандидата
  (та же карта, тот же класс или архетип, похожие винрейт и популярность).

Правила ответа:
- Выбирай кандидатов только из переданных ref (archetypes, classes, cards.*).
  Не придумывай ref, числа, посты или ссылки.
- До 8 кандидатов на нерф и до 8 на бафф, от самого вероятного. Лучше меньше,
  но обоснованно. Если сила архетипа держится на конкретной карте, предпочти
  карту (kind=card) и назови архетип в вердикте; архетип отдельно — только
  если нерф нужен всей колоде.
- Blizzard обычно нерфит то, что одновременно сильное и популярное (винрейт
  заметно выше 53–55% при большой доле игр, особенно на Легенде), и баффает
  классы/архетипы далеко ниже 47–48% с малой популярностью и новые карты,
  которые никто не играет.
- confidence 0–100: 80+ только при сильной статистике и согласии сообщества;
  40–60 — заметный, но спорный сигнал; ниже 30 не включай.
- verdict — 3–6 предложений по-русски с конкретными цифрами из данных и
  периодами. reasons — 2–5 коротких пунктов. suggested_change — одно
  правдоподобное изменение (например, «стоимость 2 → 3»).
- sentiment и community_stance — по постам об этой сущности; если упоминаний
  нет, ставь no_data и пустой social_summary.
- evidence_post_ids — только id из social.posts, 0–5 штук, самые показательные.
- Опирайся на прошлые решения Blizzard: сверяй цифры кандидата с порогами из
  history.patterns и учитывай сроки (сколько дней прошло с выхода набора, как
  давно был балансный патч). Если есть похожий прошлый случай, назови его в
  вердикте (карта, патч, что поменяли и с какими цифрами) и укажи его id в
  precedent_ids (0–3, только из history.precedents этого кандидата).
- Названия карт пиши по-русски, как в name; английское можно в скобках.
- summary — общий вывод по формату: 2–4 абзаца по-русски, без маркдаун-заголовков.
"""


class AnalysisError(RuntimeError):
    pass


@dataclass
class AnalysisResult:
    output: ModelOutput
    model: str
    usage: dict[str, Any]
    request_id: str | None = None
    dropped: list[str] = field(default_factory=list)


def analysis_model() -> str:
    return os.environ.get("RADAR_ANALYSIS_MODEL", "").strip() or DEFAULT_MODEL


_TAGS = re.compile(r"<[^>]+>|\[x\]")


def _compact_card(record: Mapping[str, Any]) -> dict[str, Any]:
    stats = record["stats"]
    return {
        "ref": record["ref"],
        "name": record["name"],
        "nameEn": record.get("nameEn"),
        "class": record.get("class"),
        "cost": record.get("cost"),
        "rarity": record.get("rarity"),
        "set": record.get("set"),
        "newSet": record.get("newSet"),
        "text": _TAGS.sub("", str(record.get("text") or "")).replace("\n", " ")[:240]
        or None,
        "winrate": stats.get("winrate"),
        "popularity": stats.get("popularity"),
        "playedWinrate": stats.get("playedWinrate"),
        "timesPlayed": stats.get("timesPlayed"),
        "rank": stats.get("rank"),
        "period": stats.get("period"),
        "trend": stats.get("trend"),
        "periods": [
            [
                p["rank"],
                p["period"],
                p.get("deckWinrate"),
                p.get("deckPopularity"),
                p.get("timesPlayed"),
            ]
            for p in stats.get("periods") or []
        ],
        "scores": record.get("scores"),
    }


def _compact_archetype(record: Mapping[str, Any]) -> dict[str, Any]:
    stats = record["stats"]
    return {
        "ref": record["ref"],
        "name": record["name"],
        "class": record.get("class"),
        "winrate": stats.get("winrate"),
        "popularity": stats.get("popularity"),
        "games": stats.get("games"),
        "legendWinrate": stats.get("legendWinrate"),
        "legendPopularity": stats.get("legendPopularity"),
        "trend": stats.get("trend"),
        "periods": [
            [
                p["rank"],
                p["period"],
                p.get("winrate"),
                p.get("popularity"),
                p.get("games"),
            ]
            for p in stats.get("periods") or []
        ],
        "scores": record.get("scores"),
    }


def _compact_class(record: Mapping[str, Any]) -> dict[str, Any]:
    stats = record["stats"]
    return {
        "ref": record["ref"],
        "name": record["name"],
        "winrate": stats.get("winrate"),
        "popularity": stats.get("popularity"),
        "games": stats.get("games"),
        "topArchetypes": stats.get("topArchetypes"),
        "scores": record.get("scores"),
    }


def select_posts(posts: Iterable[SocialPost], mentioned: set[str]) -> list[SocialPost]:
    """Balance talk and posts naming a candidate, most-upvoted first."""
    relevant = [
        post for post in posts if post.id in mentioned or post.is_balance_related()
    ]
    relevant.sort(
        key=lambda post: (post.official and post.is_balance_related(), post.score),
        reverse=True,
    )
    top_level = [post for post in relevant if post.kind != "comment"][:MAX_POSTS]
    comments = [post for post in relevant if post.kind == "comment"][:MAX_COMMENTS]
    return top_level + comments


def build_model_input(
    *,
    stats: StatsBundle,
    posts: list[SocialPost],
    mentions: Mapping[str, list[SocialPost]],
    social_window: Mapping[str, Any],
    patch: Mapping[str, Any] | None,
    previous: Mapping[str, Any] | None,
    generated_at: str,
    history: Mapping[str, Any] | None = None,
    precedents: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    shown = {post.id for post in posts}
    return {
        "format": stats.format,
        "generatedAt": generated_at,
        "patch": patch,
        "statsPeriods": stats.periods,
        "archetypes": [_compact_archetype(r) for r in stats.archetypes],
        "classes": [_compact_class(r) for r in stats.classes],
        "cards": {
            "nerfPool": [_compact_card(r) for r in stats.card_nerf_pool],
            "buffPool": [_compact_card(r) for r in stats.card_buff_pool],
        },
        "social": {
            "window": dict(social_window),
            "mentions": {
                ref: {
                    "reddit": sum(1 for post in matches if post.platform == "reddit"),
                    "x": sum(1 for post in matches if post.platform == "x"),
                    "postIds": [post.id for post in matches if post.id in shown][:6],
                }
                for ref, matches in mentions.items()
                if ref in stats.entities
            },
            "posts": [
                {
                    "id": post.id,
                    "platform": post.platform,
                    "kind": post.kind,
                    "community": post.community,
                    "author": post.author,
                    "official": post.official,
                    "score": post.score,
                    "comments": post.comments,
                    "createdAt": post.created_at,
                    "parentId": post.parent_id,
                    "title": post.title,
                    "text": post.text[:500],
                }
                for post in posts
            ],
        },
        "previousRun": previous,
        "history": {"patterns": history, "precedents": dict(precedents or {})}
        if history
        else None,
    }


def _cost(model: str, usage: Mapping[str, int]) -> float | None:
    prices = PRICES_PER_MTOK.get(model)
    if prices is None:
        return None
    input_price, output_price, read_price, write_price = prices
    return round(
        (
            usage["inputTokens"] * input_price
            + usage["outputTokens"] * output_price
            + usage["cacheReadInputTokens"] * read_price
            + usage["cacheCreationInputTokens"] * write_price
        )
        / 1_000_000,
        4,
    )


def validate_output(
    output: ModelOutput,
    allowed_refs: set[str],
    allowed_posts: set[str],
    allowed_precedents: set[str] | None = None,
) -> tuple[ModelOutput, list[str]]:
    """Drop unknown refs, post and precedent ids, duplicates and anything past the cap."""
    dropped: list[str] = []
    seen: set[str] = set()
    precedents = allowed_precedents or set()

    def clean(items: list[Any], side: str) -> list[Any]:
        kept = []
        for item in items:
            if item.ref not in allowed_refs:
                dropped.append(f"{side}:{item.ref}:unknown_ref")
                continue
            if item.ref in seen:
                dropped.append(f"{side}:{item.ref}:duplicate")
                continue
            if len(kept) >= MAX_CANDIDATES:
                dropped.append(f"{side}:{item.ref}:over_limit")
                continue
            seen.add(item.ref)
            dropped.extend(
                f"{side}:{item.ref}:post:{pid}"
                for pid in item.evidence_post_ids
                if pid not in allowed_posts
            )
            dropped.extend(
                f"{side}:{item.ref}:precedent:{pid}"
                for pid in item.precedent_ids
                if pid not in precedents
            )
            kept.append(
                item.model_copy(
                    update={
                        "evidence_post_ids": [
                            pid
                            for pid in item.evidence_post_ids
                            if pid in allowed_posts
                        ][:5],
                        "precedent_ids": [
                            pid for pid in item.precedent_ids if pid in precedents
                        ][:4],
                    }
                )
            )
        return kept

    nerfs = clean(output.nerfs, "nerfs")
    buffs = clean(output.buffs, "buffs")
    return output.model_copy(update={"nerfs": nerfs, "buffs": buffs}), dropped


def parse_message(message: Any, output_model: type[BaseModel] = ModelOutput) -> Any:
    """Validate the structured-output text block against ``output_model``."""
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise AnalysisError(f"model refused: {getattr(details, 'category', None)}")
    if stop_reason == "max_tokens":
        raise AnalysisError("model output hit max_tokens")
    text = "".join(
        str(getattr(block, "text", "") or "")
        for block in getattr(message, "content", None) or []
        if getattr(block, "type", None) == "text"
    )
    if not text.strip():
        raise AnalysisError("model returned no text block")
    try:
        return output_model.model_validate_json(text)
    except ValidationError as exc:
        raise AnalysisError(
            f"model output failed schema validation: {exc.error_count()} errors"
        ) from exc


def _record(started: float, **kwargs: Any) -> None:
    provider_usage.record_call(
        "anthropic",
        url="https://api.anthropic.com/v1/messages",
        started=started,
        source_id="balance_radar",
        **kwargs,
    )


def call_structured(
    payload: Mapping[str, Any],
    *,
    system: str,
    output_model: type[BaseModel],
    model: str | None = None,
    client: Any = None,
    effort: str = "high",
) -> tuple[Any, str, dict[str, Any], str | None]:
    """One streamed structured-output request: (parsed, served model, usage, request id)."""
    import anthropic

    requested = model or analysis_model()
    if client is None:
        try:
            client = anthropic.Anthropic(max_retries=3, timeout=900.0)
        except anthropic.AnthropicError:
            raise AnalysisError("ANTHROPIC_API_KEY is not configured") from None
    started = time.monotonic()
    request: dict[str, Any] = {
        "model": requested,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "system": [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ],
        "output_format": output_model,
    }
    if requested.startswith("claude-sonnet-5-5"):
        # Re-run a classifier decline on Anthropic's recommended fallback.
        request.update(betas=[FALLBACK_BETA], fallbacks="default")
    try:
        with client.beta.messages.stream(**request) as stream:
            message = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        _record(
            started,
            status=exc.status_code,
            outcome="failed",
            reason=f"http_{exc.status_code}",
        )
        raise AnalysisError(f"Claude API returned HTTP {exc.status_code}") from None
    except anthropic.APIConnectionError:
        _record(started, outcome="failed", reason="transport")
        raise AnalysisError("Claude API connection failed") from None
    except anthropic.AnthropicError as exc:
        # Missing credentials and other client-side failures.
        raise AnalysisError(f"Claude client error: {type(exc).__name__}") from None
    except ValidationError as exc:
        # The SDK parses ``output_format`` itself while assembling the message.
        _record(started, status=200, outcome="failed", reason="invalid_output")
        raise AnalysisError(
            f"model output failed schema validation: {exc.error_count()} errors"
        ) from None
    raw_usage = getattr(message, "usage", None)
    usage = {
        "inputTokens": int(getattr(raw_usage, "input_tokens", 0) or 0),
        "outputTokens": int(getattr(raw_usage, "output_tokens", 0) or 0),
        "cacheReadInputTokens": int(
            getattr(raw_usage, "cache_read_input_tokens", 0) or 0
        ),
        "cacheCreationInputTokens": int(
            getattr(raw_usage, "cache_creation_input_tokens", 0) or 0
        ),
    }
    served_by = str(getattr(message, "model", None) or requested)
    usage["costUsd"] = _cost(served_by, usage)
    usage["durationSeconds"] = round(time.monotonic() - started, 1)
    usage["fallbackUsed"] = any(
        getattr(item, "type", None) == "fallback_message"
        for item in getattr(raw_usage, "iterations", None) or []
    )
    try:
        parsed = parse_message(message, output_model)
    except AnalysisError:
        _record(
            started,
            status=200,
            outcome="failed",
            reason="invalid_output",
            cost=usage["costUsd"] or 0,
            cost_basis="estimated",
        )
        raise
    _record(
        started,
        status=200,
        cost=usage["costUsd"] or 0,
        cost_basis="estimated",
        profile=served_by,
    )
    return parsed, served_by, usage, getattr(message, "_request_id", None)


def analyze(
    model_input: Mapping[str, Any],
    *,
    allowed_refs: set[str],
    allowed_posts: set[str],
    allowed_precedents: set[str] | None = None,
    model: str | None = None,
    client: Any = None,
) -> AnalysisResult:
    output, served_by, usage, request_id = call_structured(
        model_input,
        system=SYSTEM_PROMPT,
        output_model=ModelOutput,
        model=model,
        client=client,
    )
    output, dropped = validate_output(
        output, allowed_refs, allowed_posts, allowed_precedents
    )
    if dropped:
        logger.warning(
            "balance radar dropped %d model items: %s", len(dropped), dropped[:10]
        )
    return AnalysisResult(
        output=output,
        model=served_by,
        usage=usage,
        request_id=request_id,
        dropped=dropped,
    )
