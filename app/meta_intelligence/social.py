from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class SocialEvidence:
    platform: Literal["x", "reddit"]
    author_reference: str
    published_at: datetime
    canonical_url: str
    deck_code: str | None
    archetype_hint: str | None
    trust_tier: Literal["allowlisted", "unverified"]
    deletion_state: Literal["available", "deleted", "unknown"]
    provenance: str


class SocialAdapter(Protocol):
    async def collect(self, *, since: datetime) -> list[SocialEvidence]: ...


class ConfirmedDraftWriter(Protocol):
    """Future adapter: confirmed structured evidence in, unpublished draft out.

    Implementations get no scraper, confidence mutation, or publishing capability.
    """
    async def draft(self, confirmed_signal: dict) -> str: ...
