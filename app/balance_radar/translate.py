"""Full Russian translations of the Reddit and X posts the radar shows.

Every post in ``topPosts`` and every quote gets ``titleRu`` and ``textRu``: the
whole title and text, not the excerpt. Claude translates in batches with the
run's name glossary; results are cached by post id and a hash of the original
(``<data>/cache/balance-radar/translations.json``), so a post costs once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..storage import root_dir
from .analysis import AnalysisError, call_structured

logger = logging.getLogger(__name__)

BATCH = 25
MAX_TEXT = 4000
CACHE_ENTRIES = 5000


class PostTranslation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title_ru: str = Field(description="Перевод заголовка; пусто, если заголовка нет")
    text_ru: str = Field(description="Полный перевод текста на русский")


class PostTranslations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posts: list[PostTranslation]


TRANSLATE_PROMPT = """Переведи посты Reddit и X о Hearthstone на русский полностью, без
сокращений и пересказа, сохраняя тон, абзацы и смысл. Названия карт, архетипов и
классов пиши по-русски строго по glossary (английских названий и скобок с ними
не оставляй, без транслитерации). Ранги: Legend — Легенда, Diamond — Алмаз.
Blizzard — компания, не переводи. Ссылки, ники и коды колод оставь как есть.
Верни перевод для каждого id из входа; title_ru пустой, если заголовка нет.
"""


def _cache_path() -> Path:
    path = root_dir() / "cache" / "balance-radar"
    path.mkdir(parents=True, exist_ok=True)
    return path / "translations.json"


def _load() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(cache: Mapping[str, Any]) -> None:
    items = list(cache.items())[-CACHE_ENTRIES:]
    path = _cache_path()
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(dict(items), ensure_ascii=False), encoding="utf-8")
    os.replace(pending, path)


def _digest(title: str | None, text: str) -> str:
    return hashlib.sha256(f"{title or ''}\n{text}".encode()).hexdigest()[:16]


def translate_posts(
    posts: Iterable[Mapping[str, Any]],
    *,
    glossary_lines: str = "",
    claude: Any = None,
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """{id: {titleRu, textRu}} for posts given as {id, title, text}; plus usage."""
    cache = _load()
    wanted: dict[str, Mapping[str, Any]] = {}
    for post in posts:
        text = str(post.get("text") or "")[:MAX_TEXT]
        if not post.get("id") or not (text.strip() or post.get("title")):
            continue
        wanted[str(post["id"])] = {
            **post,
            "text": text,
            "digest": _digest(post.get("title"), text),
        }
    missing = [
        p
        for pid, p in wanted.items()
        if (cache.get(pid) or {}).get("digest") != p["digest"]
    ]
    usage: dict[str, Any] = {
        "posts": len(wanted),
        "translated": 0,
        "cached": len(wanted) - len(missing),
        "calls": 0,
        "costUsd": 0.0,
        "errors": [],
    }
    for start in range(0, len(missing), BATCH):
        batch = missing[start : start + BATCH]
        payload = {
            "glossary": glossary_lines,
            "posts": [
                {"id": p["id"], "title": p.get("title") or "", "text": p["text"]}
                for p in batch
            ],
        }
        try:
            output, _model, call_usage, _req = call_structured(
                payload,
                system=TRANSLATE_PROMPT,
                output_model=PostTranslations,
                client=claude,
                effort="low",
            )
        except AnalysisError as exc:
            usage["errors"].append(str(exc))
            logger.warning("post translation batch skipped: %s", exc)
            continue
        usage["calls"] += 1
        usage["costUsd"] = round(
            usage["costUsd"] + float(call_usage.get("costUsd") or 0), 4
        )
        by_id = {p["id"]: p for p in batch}
        for item in output.posts:
            source = by_id.get(item.id)
            if source is None or not item.text_ru.strip() and source["text"].strip():
                continue
            cache[item.id] = {
                "digest": source["digest"],
                "titleRu": item.title_ru.strip(),
                "textRu": item.text_ru.strip(),
            }
            usage["translated"] += 1
    if missing:
        _save(cache)
    result = {
        pid: {
            "titleRu": cache[pid].get("titleRu") or None,
            "textRu": cache[pid].get("textRu") or None,
        }
        for pid, post in wanted.items()
        if (cache.get(pid) or {}).get("digest") == post["digest"]
    }
    return result, usage
