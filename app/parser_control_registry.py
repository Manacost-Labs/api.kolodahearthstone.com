from __future__ import annotations

from dataclasses import dataclass

from .hsreplay_card_periods import HSREPLAY_CARD_PERIOD_SOURCE_IDS
from .post_patch_policy import EARLY_SOURCE_IDS
from .sources import SOURCE_BY_ID
from .trinket_slices import TRINKET_SLICE_SOURCE_IDS


@dataclass(frozen=True)
class ParserSection:
    id: str
    label: str
    group: str
    description: str
    source_ids: tuple[str, ...]

    @property
    def supports_early(self) -> bool:
        return any(source_id in EARLY_SOURCE_IDS for source_id in self.source_ids)


SECTIONS: tuple[ParserSection, ...] = (
    ParserSection(
        id="traditional-standard-meta",
        label="Мета Standard",
        group="Традиционный режим",
        description="Архетипы, распределение классов и ранговая мета Standard.",
        source_ids=(
            "hsguru_meta_standard_legend",
            "hsguru_meta_standard_diamond_4to1",
            "hsguru_meta_standard_top_5k",
            "hsguru_meta_standard_top_legend",
            "hsguru_meta_matrix",
            "hsreplay_meta_archetypes_legend_eu_1d",
            "hsreplay_meta_top_1000_legend_1d_firecrawl",
            "hsreplay_meta_legend_1d_firecrawl",
            "hsreplay_meta_diamond_4to1_1d_firecrawl",
            "firestone_standard",
            "metastats_decks",
            "vicious_syndicate_live_beta",
            "hsreplay_archetypes",
        ),
    ),
    ParserSection(
        id="traditional-wild-meta",
        label="Мета Wild",
        group="Традиционный режим",
        description="Архетипы и распределение классов в Вольном формате.",
        source_ids=(
            "hsguru_meta_wild_legend",
            "hsguru_meta_wild_diamond_4to1",
            "hsguru_meta_wild_top_legend",
            "hsguru_meta_wild_top_5k",
        ),
    ),
    ParserSection(
        id="traditional-matchups",
        label="Матчапы",
        group="Традиционный режим",
        description="Матрицы матчапов и радары взаимодействий между колодами.",
        source_ids=(
            "hsguru_matchups_legend",
            "hsguru_matchups_wild_legend",
            "hsguru_matchups_diamond_4to1",
            "hsguru_archetype_analysis",
            "metastats_matchups",
            "vicious_syndicate_radars",
        ),
    ),
    ParserSection(
        id="traditional-cards",
        label="Карты",
        group="Традиционный режим",
        description="Статистика карт Standard и Wild по рангам.",
        source_ids=(
            "hsreplay_cards_legend_included_winrate",
            "hsreplay_cards_legend_included_popularity",
            *HSREPLAY_CARD_PERIOD_SOURCE_IDS,
        ),
    ),
    ParserSection(
        id="traditional-decks",
        label="Колоды",
        group="Традиционный режим",
        description="Популярные, стримерские, фановые и высокоранговые сборки.",
        source_ids=(
            "hsguru_streamer_decks_legend_1000",
            "hsguru_fun_decks",
            "hsreplay_decks_trending",
            "hearthstone_decks",
        ),
    ),
    ParserSection(
        id="arena-classes",
        label="Классы Арены",
        group="Арена",
        description="Винрейты, популярность и результаты классов Арены.",
        source_ids=(
            "hsreplay_arena",
            "hsreplay_arena_class_pages_firecrawl",
        ),
    ),
    ParserSection(
        id="arena-tier-list",
        label="Тир-лист Арены",
        group="Арена",
        description="Оценки карт HSReplay, HearthArena и Firestone.",
        source_ids=(
            "hsreplay_arena_cards_advanced",
            "heartharena_tierlist",
            "firestone_arena_cards_normal",
            "firestone_arena_cards_underground",
        ),
    ),
    ParserSection(
        id="arena-legendaries",
        label="Легендарные карты Арены",
        group="Арена",
        description="Статистика легендарных карт в обычной и Подземной Арене.",
        source_ids=(
            "hsreplay_arena_legendaries",
            "firestone_arena_legendaries_normal",
            "firestone_arena_legendaries_underground",
        ),
    ),
    ParserSection(
        id="arena-winning-decks",
        label="Победные колоды Арены",
        group="Арена",
        description="Сборки колод с успешных забегов Арены.",
        source_ids=("hsreplay_arena_winning_decks",),
    ),
    ParserSection(
        id="battlegrounds-heroes",
        label="Герои Полей Сражений",
        group="Поля Сражений",
        description="Тир-лист героев и подробная статистика по рейтингам.",
        source_ids=(
            "hsreplay_battlegrounds_heroes",
            "hsreplay_battlegrounds_hero_details",
        ),
    ),
    ParserSection(
        id="battlegrounds-cards",
        label="Карты Полей Сражений",
        group="Поля Сражений",
        description="Существа и заклинания по уровням таверны.",
        source_ids=(
            "hsreplay_battlegrounds_minions",
            "firestone_battlegrounds_cards",
            "firestone_battlegrounds_spells",
        ),
    ),
    ParserSection(
        id="battlegrounds-compositions",
        label="Составы Полей Сражений",
        group="Поля Сражений",
        description="Композиции и синергии из HSReplay и Firestone.",
        source_ids=(
            "hsreplay_battlegrounds_comps",
            "hsreplay_battlegrounds_compositions",
            "hsreplay_battlegrounds_compositions_screenshot",
            "firestone_battlegrounds_comps",
        ),
    ),
    ParserSection(
        id="battlegrounds-trinkets",
        label="Аксессуары Полей Сражений",
        group="Поля Сражений",
        description="Малые и большие аксессуары с актуальной статистикой.",
        source_ids=(
            "hsreplay_battlegrounds_trinkets_lesser",
            "hsreplay_battlegrounds_trinkets_greater",
            *sorted(TRINKET_SLICE_SOURCE_IDS),
        ),
    ),
)


SECTION_BY_ID = {section.id: section for section in SECTIONS}
SOURCE_TO_SECTION = {
    source_id: section.id
    for section in SECTIONS
    for source_id in section.source_ids
}

_duplicates = [
    source_id
    for source_id in SOURCE_BY_ID
    if sum(source_id in section.source_ids for section in SECTIONS) != 1
]
if _duplicates:
    raise RuntimeError(
        "Parser control registry must contain every source exactly once: "
        + ", ".join(sorted(_duplicates))
    )


SOURCE_LABELS_RU: dict[str, str] = {
    "hsguru_streamer_decks_legend_1000": "HSGuru · колоды стримеров, топ-1000 Легенды",
    "hsguru_fun_decks": "HSGuru · фановые / off-meta колоды",
    "hsguru_meta_standard_legend": "HSGuru · Standard, Легенда",
    "hsguru_meta_standard_diamond_4to1": "HSGuru · Standard, Алмаз 4–1",
    "hsguru_meta_wild_legend": "HSGuru · Wild, Легенда",
    "hsguru_meta_wild_diamond_4to1": "HSGuru · Wild, Алмаз 4–1",
    "hsguru_meta_standard_top_5k": "HSGuru · Standard, топ-5000",
    "hsguru_meta_standard_top_legend": "HSGuru · Standard, верх Легенды",
    "hsguru_meta_matrix": "HSGuru · единая матрица Standard/Wild",
    "hsguru_meta_wild_top_legend": "HSGuru · Wild, верх Легенды",
    "hsguru_meta_wild_top_5k": "HSGuru · Wild, топ-5000",
    "hsguru_matchups_legend": "HSGuru · Standard, матчапы Легенды",
    "hsguru_matchups_wild_legend": "HSGuru · Wild, матчапы Легенды",
    "hsguru_matchups_diamond_4to1": "HSGuru · матчапы Алмаза 4–1",
    "hsguru_archetype_analysis": "HSGuru · анализ архетипов",
    "hsreplay_battlegrounds_comps": "HSReplay · составы Полей Сражений",
    "hsreplay_battlegrounds_heroes": "HSReplay · герои Полей Сражений",
    "hsreplay_battlegrounds_minions": "HSReplay · существа Полей Сражений",
    "hsreplay_battlegrounds_compositions": "HSReplay · статистика составов",
    "hsreplay_battlegrounds_compositions_screenshot": (
        "HSReplay · снимок составов Полей Сражений"
    ),
    "hsreplay_battlegrounds_trinkets_lesser": "HSReplay · малые аксессуары",
    "hsreplay_battlegrounds_trinkets_greater": "HSReplay · большие аксессуары",
    "hsreplay_arena": "HSReplay · обзор Арены",
    "hsreplay_arena_legendaries": "HSReplay · легендарные карты Арены",
    "hsreplay_arena_winning_decks": "HSReplay · победные колоды Арены",
    "hsreplay_arena_cards_advanced": "HSReplay · расширенная статистика карт Арены",
    "hsreplay_decks_trending": "HSReplay · популярные колоды",
    "hsreplay_cards_legend_included_winrate": "HSReplay · карты по винрейту колод",
    "hsreplay_cards_legend_included_popularity": "HSReplay · карты по популярности",
    "hsreplay_cards_legend_1d": "HSReplay · карты Standard, Легенда",
    "hsreplay_cards_legend_3d": "HSReplay · карты Standard, Легенда, 3 дня",
    "hsreplay_cards_legend_7d": "HSReplay · карты Standard, Легенда, 7 дней",
    "hsreplay_cards_legend_14d": "HSReplay · карты Standard, Легенда, 14 дней",
    "hsreplay_cards_legend_patch": "HSReplay · карты Standard, текущий патч",
    "hsreplay_cards_wild_legend_1d": "HSReplay · карты Wild, Легенда",
    "hsreplay_cards_wild_legend_3d": "HSReplay · карты Wild, Легенда, 3 дня",
    "hsreplay_cards_wild_legend_7d": "HSReplay · карты Wild, Легенда, 7 дней",
    "hsreplay_cards_wild_legend_14d": "HSReplay · карты Wild, Легенда, 14 дней",
    "hsreplay_cards_wild_legend_patch": "HSReplay · карты Wild, текущий патч",
    "hsreplay_meta_archetypes_legend_eu_1d": "HSReplay · архетипы Standard, Легенда EU",
    "hsreplay_meta_top_1000_legend_1d_firecrawl": "HSReplay · мета топ-1000 Легенды",
    "hsreplay_meta_legend_1d_firecrawl": "HSReplay · мета Легенды",
    "hsreplay_meta_diamond_4to1_1d_firecrawl": "HSReplay · мета Алмаза 4–1",
    "hsreplay_arena_class_pages_firecrawl": "HSReplay · страницы классов Арены",
    "firestone_standard": "Firestone · мета Standard, Легенда, последний патч",
    "firestone_battlegrounds_comps": "Firestone · составы Полей Сражений",
    "firestone_battlegrounds_cards": "Firestone · существа Полей Сражений",
    "firestone_battlegrounds_spells": "Firestone · заклинания Полей Сражений",
    "firestone_arena_cards_normal": "Firestone · карты обычной Арены",
    "firestone_arena_cards_underground": "Firestone · карты Подземной Арены",
    "firestone_arena_legendaries_underground": "Firestone · легендарки Подземной Арены",
    "firestone_arena_legendaries_normal": "Firestone · легендарки обычной Арены",
    "heartharena_tierlist": "HearthArena · тир-лист карт",
    "metastats_decks": "MetaStats · архетипы и колоды",
    "metastats_matchups": "MetaStats · матчапы",
    "hearthstone_decks": "Hearthstone-Decks · высокоранговые колоды",
    "vicious_syndicate_radars": "Vicious Syndicate · радары колод",
    "vicious_syndicate_live_beta": "Vicious Syndicate Live · Standard",
    "hsreplay_battlegrounds_hero_details": "HSReplay · подробности героев (служебная задача)",
    "hsreplay_archetypes": "HSReplay · база архетипов (служебная задача)",
}


def section_for_source(source_id: str) -> ParserSection | None:
    section_id = SOURCE_TO_SECTION.get(source_id)
    return SECTION_BY_ID.get(section_id) if section_id else None


def source_label(source_id: str) -> str:
    source = SOURCE_BY_ID[source_id]
    return SOURCE_LABELS_RU.get(source_id) or source.description or source.id
