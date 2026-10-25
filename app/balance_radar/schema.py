"""Schemas: what Claude must return, and the public Balance Radar contract.

The model chooses candidates and writes the judgement (confidence, verdict,
reasons, sentiment). Numbers, post URLs, authors and scores never come from
the model: the pipeline copies them from the datasets and collected posts by
``ref`` and post id, so a hallucinated statistic cannot reach the API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
Sentiment = Literal["negative", "mixed", "neutral", "positive", "no_data"]
Stance = Literal["wants_nerf", "wants_buff", "divided", "indifferent", "no_data"]
CandidateKind = Literal["card", "archetype", "class"]

# -- model output (structured outputs) ---------------------------------------


class ModelCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(description="ref кандидата, ровно как во входных данных")
    confidence: int = Field(
        ge=0, le=100, description="Уверенность 0-100, что изменение произойдёт/нужно"
    )
    headline: str = Field(description="Короткий вывод в одну строку, по-русски")
    verdict: str = Field(
        description="Подробный вердикт на русском, 3-6 предложений с цифрами"
    )
    reasons: list[str] = Field(description="2-5 конкретных причин на русском")
    suggested_change: str = Field(
        description="Правдоподобное конкретное изменение, по-русски"
    )
    sentiment: Sentiment = Field(description="Тон обсуждений этой сущности")
    community_stance: Stance = Field(description="Чего хочет сообщество")
    social_summary: str = Field(
        description="Что говорят в соцсетях, по-русски; пусто, если данных нет"
    )
    evidence_post_ids: list[str] = Field(
        description="id постов из входных данных, подтверждающих вывод"
    )


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="Общий вывод по формату на русском, 2-4 абзаца")
    nerfs: list[ModelCandidate] = Field(
        description="Кандидаты на нерф, от самого вероятного"
    )
    buffs: list[ModelCandidate] = Field(
        description="Кандидаты на бафф, от самого вероятного"
    )


# -- public contract -----------------------------------------------------------


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RadarPost(_Open):
    platform: Literal["reddit", "x"]
    kind: Literal["post", "comment", "tweet"]
    id: str
    url: str
    author: str
    score: int
    comments: int | None = None
    title: str | None = None
    excerpt: str
    community: str
    createdAt: str | None = None
    official: bool = False
    citedByModel: bool = False


class RadarMentions(_Open):
    total: int = Field(ge=0)
    reddit: int = Field(ge=0)
    x: int = Field(ge=0)


class RadarSocial(_Open):
    mentions: RadarMentions
    sentiment: Sentiment
    stance: Stance
    summary: str
    topPosts: list[RadarPost]


class RadarStats(_Open):
    source: Literal["hsguru", "hsreplay"]
    rank: str
    period: str
    winrate: float | None = None
    popularity: float | None = None
    games: int | None = None
    timesPlayed: int | None = None
    playedWinrate: float | None = None
    trend: dict[str, Any] | None = None
    periods: list[dict[str, Any]] = Field(default_factory=list)


class RadarDates(_Open):
    firstFlaggedAt: str
    previousConfidence: int | None = None
    statsFetchedAt: str | None = None
    socialWindowFrom: str | None = None
    socialWindowTo: str | None = None


class RadarCandidate(_Open):
    ref: str
    kind: CandidateKind
    id: str
    dbfId: int | None = None
    name: str
    nameEn: str | None = None
    class_: str | None = Field(default=None, alias="class")
    className: str | None = None
    imageUrl: str | None = None
    confidence: int = Field(ge=0, le=100)
    headline: str
    verdict: str
    reasons: list[str]
    suggestedChange: str
    stats: RadarStats
    social: RadarSocial
    dates: RadarDates


class RadarSource(_Open):
    id: str
    kind: Literal["stats", "social"]
    platform: str | None = None
    state: str = "ok"
    fetchedAt: str | None = None
    items: int | None = None
    error: str | None = None


class RadarResult(_Open):
    schemaVersion: int = SCHEMA_VERSION
    runId: str
    format: Literal["standard", "wild"]
    generatedAt: str
    summary: str
    nerfs: list[RadarCandidate]
    buffs: list[RadarCandidate]
    patch: dict[str, Any] | None = None
    statsPeriods: dict[str, Any]
    model: str
    requestedModel: str
    sources: list[RadarSource]
    socialWindow: dict[str, Any]
    usage: dict[str, Any]
