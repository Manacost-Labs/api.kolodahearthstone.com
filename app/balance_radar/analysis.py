"""Claude analysis of one format: statistics plus social digest in, JSON out."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from .. import provider_usage
from .schema import ModelOutput
from .social import SocialPost
from .stats import StatsBundle

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 128_000
MAX_CANDIDATES = 25
MAX_QUOTES = 3
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
Твоя задача: по статистике, обсуждениям в соцсетях и прошлым решениям Blizzard
определить, какие карты, архетипы и классы вероятнее всего понерфят или
забаффают в ближайшем балансном патче, и объяснить почему цифрами.

Как читать данные:
- Архетипы и классы — HSGuru. Заголовочный срез — ранг all в периоде
  statsPeriods.primary (обычно окно «Bans» *_ban: игры после последнего
  балансного/бан-обновления, самое свежее состояние). ranks — тот же период на
  рангах all, legend, top_legend (топ Легенды) и diamond_4to1. winrate и
  popularity — в процентах; popularity — доля игр. trend.winrateDelta/
  popularityDelta — past_day минус past_week; banWinrateDelta — окно банов минус
  всё дополнение.
- mulligan (HSGuru) — влияние карты на винрейт колоды в п.п.: mulliganImpact —
  оставили в стартовой руке, drawnImpact — взяли за игру, keptImpact — оставили
  при муллигане. Высокий плюс означает, что сила колоды держится на этой карте.
- Карты — HSReplay (Legend, 7 дней; Diamond и Diamond 4-1 в periods). winrate —
  винрейт колод с картой, popularity — доля колод с картой. playedWinrate и
  drawnWinrate завышены у дорогих карт — только вспомогательный сигнал.
  core — архетипы HSReplay Legend, в которых карта стоит в ≥50% колод, с их
  винрейтом и популярностью за неделю (те же признаки, что в калибровке).
- calibration (у кандидатов) — {nerf: [доля, наблюдений], buff: [...]}: как часто
  Blizzard реально меняла архетип с такими винрейтом и популярностью (HSReplay
  Legend; у карт — их core-архетип) в следующем балансном патче. Это якорь для
  confidence.
- Компактные массивы: ranks — {ранг: [winrate, popularity, games]}; periods
  архетипов — [ранг, период, winrate, popularity, games]; periods карт —
  [ранг, период, winrate колод, popularity, розыгрышей]; core —
  [архетип, доля колод с картой, winrate, popularity]; mulligan у карты —
  [архетип, mulliganImpact, drawnImpact, keptImpact, взяли раз], у архетипа
  keyCards — [карта, mulliganImpact, drawnImpact, keptImpact].
- timing — сколько дней прошло с выхода дополнения и прошлого балансного
  патча и историческая вероятность патча в ближайшие 7 и 14 дней.
- social.posts — посты Reddit и X за окно; mentions — сколько постов упоминают
  сущность. developerStatements — посты разработчиков и официальных аккаунтов:
  это самый сильный социальный сигнал (заявления о планах, «следим за…»).
  Мемы и сарказм — слабый сигнал.
- previousRun — прошлый прогон: учитывай устойчивость сигналов.
- history.patterns — пороги, типичные правки, сроки патчей, эффект правок;
  history.precedents[ref] — похожие прошлые правки для кандидата.

Правила ответа:
- Выбирай кандидатов только из переданных ref. Не придумывай ref, числа, посты,
  цитаты или ссылки.
- Точность важнее количества. Включай только настоящих кандидатов: у каждого
  должно быть не меньше двух цифр из входа, которые выделяют его (перекос
  винрейта или популярности на высоких рангах, высокая калибровка, сильный
  муллиган, заявление разработчиков). Слабыми и сомнительными список не
  добивай. Обычно это 5–20 кандидатов в каждом списке, максимум 25. Карты
  (kind=card), архетипы (kind=archetype) и классы (kind=class) — разные
  кандидаты: если сила архетипа держится на карте, можно назвать обоих.
- confidence 0–100 — вероятность, что эту сущность изменят в ближайшем
  балансном патче. Ранжируй по своей оценке всех сигналов: статистика на
  высоких рангах, муллиган, сроки патча, единодушие сообщества и заявления
  разработчиков. calibration — справка о том, как часто Blizzard меняла
  похожие колоды, а не готовый ответ (бэктест показал, что слепое следование
  ей ухудшает прогноз); если сильно расходишься с ней, объясни почему.
  Кандидаты с confidence ниже 15 не включай.
- key_numbers — 2–5 строк: конкретные цифры из входа, на которых основан вывод,
  со сравнением (например «Легенда: 58,8% при 11,1% популярности — выше медианы
  нерфов 52,8% / 11%», «калибровка: 5 из 11 таких колод понерфили»).
- Пиши коротко и для читателя сайта: verdict — 1–2 предложения, headline —
  до 10 слов, reasons — 2–3 пункта до 12 слов, meta_impact — одно предложение,
  key_numbers — до 3 строк. Одна цифра — один раз.
- predicted_changes — 1–3 конкретных варианта правки с вероятностями (сумма
  не больше 100), с field (cost, attack, health, durability, armor, text,
  other) и before/after для чисел, опираясь на типичные правки истории
  (+1 мана, −1 атака/здоровье, текст). suggested_change — самый вероятный из них.
- meta_impact — что изменится в мете, если правка выйдет (какие колоды
  поднимутся или упадут), 1–2 предложения.
- quotes — 0–3 дословные цитаты (до 200 символов, на языке оригинала) из
  social.posts или developerStatements об этой сущности, с post_id.
- developer_signal — что говорили разработчики об этой сущности, пересказом по-русски (без английских цитат), иначе пусто.
- sentiment и community_stance — по постам об этой сущности; если упоминаний нет,
  no_data и пустой social_summary. evidence_post_ids — 0–5 id из social.posts.
- precedent_ids — 0–3 id из history.precedents этого кандидата; назови похожий
  случай в вердикте (карта, патч, что поменяли, при каких цифрах).
- timing_outlook — когда вероятен следующий балансный патч и почему, одно предложение.
- Только русские названия: карты, архетипы и классы — строго как в name и в
  glossary («English = Русский»). Никаких английских названий, английских
  дублей в скобках и транслитерации («Zoolock» → «Зоолок», «Blazing
  Invocation» → «Огненное заклятье»). Склоняй названия по-русски. Ранги:
  Легенда, Алмаз, топ Легенды («на Легенде», «в Алмазе 4–1»). Blizzard — это
  компания, пиши «Blizzard». Заголовки и тексты постов упоминай только в
  русском переводе (quotes остаются дословными, их переводит сайт).
- Никогда не упоминай имена полей, ключи JSON и служебные слова (calibration,
  history.patterns, keyNumbers, «скоры» и т. п.) — пиши обычной речью для
  читателя: «по истории патчей», «по калибровке прошлых нерфов».
- summary — общий вывод по формату: 2–3 предложения по-русски.
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


def _calibration_view(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """{side: [historical rate, samples]} — see the prompt for the meaning."""
    calibration = record.get("calibration") or {}
    view = {
        side: [item.get("historicalRate"), item.get("samples")]
        for side, item in calibration.items()
        if isinstance(item, dict) and side in {"nerf", "buff"}
    }
    return view or None


def _trend_view(trend: Any) -> Any:
    if not isinstance(trend, dict):
        return None
    return {
        key: value
        for key, value in trend.items()
        if key != "basis" and value is not None
    } or None


def _mulligan_view(mulligan: Any) -> Any:
    if isinstance(mulligan, list):  # a card: its impact in the decks running it
        return [
            [
                m["archetype"],
                m.get("mulliganImpact"),
                m.get("drawnImpact"),
                m.get("keptImpact"),
                m.get("drawnCount"),
            ]
            for m in mulligan
        ]
    if isinstance(mulligan, dict):  # an archetype: its key and weak cards
        return {
            "period": mulligan.get("period"),
            "rank": mulligan.get("rank"),
            "keyCards": [
                [
                    c["name"],
                    c.get("mulliganImpact"),
                    c.get("drawnImpact"),
                    c.get("keptImpact"),
                ]
                for c in (mulligan.get("keyCards") or [])[:4]
            ],
            "weakCards": [
                [c["name"], c.get("drawnImpact")]
                for c in (mulligan.get("weakCards") or [])[:2]
            ],
        }
    return None


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
        "text": _TAGS.sub("", str(record.get("text") or "")).replace("\n", " ")[:160]
        or None,
        "winrate": stats.get("winrate"),
        "popularity": stats.get("popularity"),
        "playedWinrate": stats.get("playedWinrate"),
        "timesPlayed": stats.get("timesPlayed"),
        "trend": _trend_view(stats.get("trend")),
        # rank, period, deck winrate, deck popularity, plays: every Legend
        # period, the seven-day window elsewhere.
        "periods": [
            [
                p["rank"],
                p["period"],
                p.get("deckWinrate"),
                p.get("deckPopularity"),
                p.get("timesPlayed"),
            ]
            for p in stats.get("periods") or []
            if (p["rank"] == "legend" and p["period"] in {"1d", "7d", "14d"})
            or (p["rank"] != "legend" and p["period"] == "7d")
        ],
        "mulligan": _mulligan_view(stats.get("mulligan")),
        "core": [
            [c["archetype"], c.get("cardShare"), c.get("winrate"), c.get("popularity")]
            for c in stats.get("core") or []
        ]
        or None,
        "calibration": _calibration_view(record),
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
        # rank: [winrate, popularity, games] in the primary period
        "ranks": {
            rank: [v.get("winrate"), v.get("popularity"), v.get("games")]
            for rank, v in (stats.get("ranks") or {}).items()
        },
        "trend": _trend_view(stats.get("trend")),
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
        "mulligan": _mulligan_view(stats.get("mulligan")),
        "calibration": _calibration_view(record),
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
        "calibration": _calibration_view(record),
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


MAX_DEVELOPER_POSTS = 15


def developer_posts(posts: Iterable[SocialPost]) -> list[SocialPost]:
    """Posts by the game's official account or Blizzard-flaired staff, newest first."""
    found = [
        post
        for post in posts
        if post.author_role
        # Support replies from the official account ("known issue") are noise.
        and not (
            post.platform == "x"
            and post.text.startswith("@")
            and not post.is_balance_related()
        )
    ]
    found.sort(key=lambda post: post.created_at or "", reverse=True)
    return found[:MAX_DEVELOPER_POSTS]


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
    timing: Mapping[str, Any] | None = None,
    developer: Sequence[SocialPost] = (),
) -> dict[str, Any]:
    shown = {post.id for post in (*posts, *developer)}
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
        "developerStatements": [
            {
                "id": post.id,
                "platform": post.platform,
                "role": post.author_role,
                "author": post.author,
                "createdAt": post.created_at,
                "score": post.score,
                "title": post.title,
                "text": post.text[:600],
            }
            for post in developer
        ],
        "timing": dict(timing) if timing else None,
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


_NORMAL_SPACE = re.compile(r"\s+")


def _normal(text: str) -> str:
    return (
        _NORMAL_SPACE.sub(" ", text.replace("\u2019", "'").replace("\u2018", "'"))
        .strip()
        .casefold()
    )


def validate_output(
    output: ModelOutput,
    allowed_refs: set[str],
    allowed_posts: set[str],
    allowed_precedents: set[str] | None = None,
    post_texts: Mapping[str, str] | None = None,
) -> tuple[ModelOutput, list[str]]:
    """Drop unknown refs, post and precedent ids, duplicates and anything past the cap."""
    dropped: list[str] = []
    seen: set[str] = set()
    precedents = allowed_precedents or set()
    bodies = {pid: _normal(text) for pid, text in (post_texts or {}).items()}

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
            quotes = []
            for quote in item.quotes:
                text = quote.text.strip().strip('"«»“”')
                if (
                    quote.post_id in bodies
                    and text
                    and _normal(text) in bodies[quote.post_id]
                ):
                    quotes.append(quote.model_copy(update={"text": text[:200]}))
                else:
                    dropped.append(f"{side}:{item.ref}:quote:{quote.post_id}")
            changes = list(item.predicted_changes)[:3]
            total = sum(change.probability for change in changes)
            if total > 100:
                changes = [
                    change.model_copy(
                        update={"probability": round(change.probability * 100 / total)}
                    )
                    for change in changes
                ]
            kept.append(
                item.model_copy(
                    update={
                        "quotes": quotes[:MAX_QUOTES],
                        "predicted_changes": changes,
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
            client = anthropic.Anthropic(max_retries=3, timeout=1800.0)
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
    post_texts: Mapping[str, str] | None = None,
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
        output, allowed_refs, allowed_posts, allowed_precedents, post_texts
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
