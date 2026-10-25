"""Schemas: what Claude must return, and the public Balance Radar contract.

The model chooses candidates and writes the judgement (confidence, verdict,
reasons, sentiment). Numbers, post URLs, authors and scores never come from
the model: the pipeline copies them from the datasets and collected posts by
``ref`` and post id, so a hallucinated statistic cannot reach the API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 2: predictedChanges, metaImpact, quotes, keyNumbers, calibration, trend,
#    confidenceHistory, timing, changesSincePrevious, counts (all additive).
SCHEMA_VERSION = 2
Sentiment = Literal["negative", "mixed", "neutral", "positive", "no_data"]
Stance = Literal["wants_nerf", "wants_buff", "divided", "indifferent", "no_data"]
CandidateKind = Literal["card", "archetype", "class"]

# -- model output (structured outputs) ---------------------------------------

ChangeField = Literal[
    "cost", "attack", "health", "durability", "armor", "text", "other"
]


class PredictedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change: str = Field(description="Изменение по-русски, например «Стоимость 2 → 3»")
    field: ChangeField
    before: int | None = Field(
        description="Значение до (для числовых полей), иначе null"
    )
    after: int | None = Field(
        description="Значение после (для числовых полей), иначе null"
    )
    probability: int = Field(
        ge=0, le=100, description="Вероятность этого варианта, если правка будет"
    )


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_id: str = Field(
        description="id поста или комментария из social.posts/developerStatements"
    )
    text: str = Field(
        description="Дословная цитата из этого поста, до 200 символов, на языке оригинала"
    )


class ModelCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(description="ref кандидата, ровно как во входных данных")
    confidence: int = Field(
        ge=0, le=100, description="Уверенность 0-100, что изменение произойдёт/нужно"
    )
    headline: str = Field(description="Короткий вывод в одну строку, по-русски")
    verdict: str = Field(
        description="Вердикт на русском, 1-2 предложения с ключевыми цифрами"
    )
    reasons: list[str] = Field(
        description="2-3 короткие причины на русском, до 12 слов"
    )
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
    precedent_ids: list[str] = Field(
        description="id прошлых случаев из history.precedents, на которые опирается вывод (0-3)"
    )
    key_numbers: list[str] = Field(
        description="2-5 цифр из входа, на которых основан вывод, со сравнением с порогом/калибровкой"
    )
    predicted_changes: list[PredictedChange] = Field(
        description="1-3 варианта конкретной правки с вероятностями (сумма не больше 100)"
    )
    meta_impact: str = Field(
        description="Ожидаемый эффект правки на мету, 1-2 предложения по-русски"
    )
    quotes: list[Quote] = Field(
        description="0-3 дословные цитаты из сообщества об этой сущности"
    )
    developer_signal: str = Field(
        description="Что говорили разработчики/официальный аккаунт об этой сущности; пусто, если ничего"
    )


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        description="Общий вывод по формату на русском, 2-3 предложения"
    )
    nerfs: list[ModelCandidate] = Field(
        description="Кандидаты на нерф, от самого вероятного"
    )
    buffs: list[ModelCandidate] = Field(
        description="Кандидаты на бафф, от самого вероятного"
    )
    timing_outlook: str = Field(
        description="Когда вероятен следующий балансный патч и почему (по timing), 1-2 предложения по-русски"
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


Direction = Literal["nerf", "buff", "rework", "neutral"]


class RadarPrecedent(_Open):
    """A past Blizzard balance change similar to a candidate (from the history dataset)."""

    id: str
    patch: str
    date: str | None = None
    cardId: str
    dbfId: int | None = None
    name: str
    nameEn: str | None = None
    class_: str | None = Field(default=None, alias="class")
    direction: Direction
    changes: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    setAgeDays: int | None = None
    preStats: dict[str, Any] | None = None
    postStats: dict[str, Any] | None = None
    url: str | None = None
    similarity: list[str] = Field(default_factory=list)
    citedByModel: bool = False


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
    precedents: list[RadarPrecedent] = Field(default_factory=list)
    keyNumbers: list[str] = Field(default_factory=list)
    predictedChanges: list[dict[str, Any]] = Field(default_factory=list)
    metaImpact: str | None = None
    quotes: list[dict[str, Any]] = Field(default_factory=list)
    developerSignal: str | None = None
    calibration: dict[str, Any] | None = None
    trend: dict[str, Any] | None = None
    confidenceHistory: list[dict[str, Any]] = Field(default_factory=list)
    textEntities: list[dict[str, Any]] = Field(default_factory=list)
    editor: dict[str, Any] | None = None


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
    history: dict[str, Any] | None = None
    timing: dict[str, Any] | None = None
    changesSincePrevious: dict[str, Any] | None = None
    counts: dict[str, Any] | None = None
    accuracy: dict[str, Any] | None = None
    editor: dict[str, Any] | None = None
    summaryEntities: list[dict[str, Any]] = Field(default_factory=list)
