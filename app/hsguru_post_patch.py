from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .hsguru_meta_matrix import resolve_current_patch_period
from .post_patch_policy import policy_for
from .sources import Source
from .storage import load_dataset

_PATCH_SCOPED_CATEGORIES = frozenset({"meta", "matchups"})


def _supports_current_patch_scope(source: Source) -> bool:
    return source.site == "hsguru" and source.category in _PATCH_SCOPED_CATEGORIES


def source_for_current_patch(
    source: Source,
    *,
    cached_matrix: dict[str, Any] | None = None,
) -> Source:
    """Return the same HSGuru view constrained to the newest known patch."""

    if not _supports_current_patch_scope(source):
        return source
    period = resolve_current_patch_period(cached_matrix)
    parsed = urlsplit(source.url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["period"] = period
    scoped_url = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )
    return replace(source, url=scoped_url)


def source_for_active_post_patch(source: Source) -> Source:
    """Scope patch-sensitive HSGuru pages only inside a bounded early window."""

    if not _supports_current_patch_scope(source) or policy_for(source.id) is None:
        return source
    try:
        cached_matrix = load_dataset("hsguru_meta_matrix")
    except (OSError, UnicodeError, ValueError):
        cached_matrix = None
    return source_for_current_patch(source, cached_matrix=cached_matrix)
