from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urldefrag

from .hsreplay_card_periods import HSREPLAY_CARD_PERIOD_SOURCE_SPECS
from .trinket_slices import TRINKET_SLICE_PARAMETERS, trinket_slice_source_id


@dataclass(frozen=True)
class Source:
    id: str
    url: str
    site: str
    category: str
    parser: str = "generic_html"
    description: str = ""
    # Per-source freshness threshold in hours; None => global HS_STALE_HOURS
    # (config.stale_dataset_hours()). Set for sources refreshed on slow cadences
    # (e.g. weekly systemd timers) so they are not reported stale between runs.
    stale_hours: float | None = None
    # "scrape" — fetched by the generic refresh pipeline (fetcher tiers);
    # "pipeline" — refreshed by a dedicated command/systemd timer, never scraped
    # by the tier planner.
    kind: str = "scrape"

    @property
    def fetch_url(self) -> str:
        return urldefrag(self.url).url

    @property
    def fragment(self) -> str:
        return urldefrag(self.url).fragment


SOURCES: tuple[Source, ...] = (
    Source(
        "hsguru_streamer_decks_legend_1000",
        "https://www.hsguru.com/streamer-decks?last_played=min_ago_60&legend=1000&violet_hold=no&limit=100",
        "hsguru",
        "streamer_decks",
        description="Streamer decks filtered to top 1000 legend, last 60 minutes, excluding Violet Hold, limit 100.",
    ),
    Source(
        "hsguru_meta_standard_legend",
        "https://www.hsguru.com/meta?format=2&min_games=100&rank=legend",
        "hsguru",
        "meta",
        description="HSGuru meta, format=2, legend (min_games=100 for full table SSR).",
    ),
    Source(
        "hsguru_meta_standard_diamond_4to1",
        "https://www.hsguru.com/meta?format=2&rank=diamond_4to1",
        "hsguru",
        "meta",
        description="HSGuru meta, format=2, diamond 4-1.",
    ),
    Source(
        "hsguru_meta_wild_legend",
        "https://www.hsguru.com/meta?format=1&min_games=100&rank=legend",
        "hsguru",
        "meta",
        description="HSGuru meta, format=1, legend (min_games=100 for full table SSR).",
    ),
    Source(
        "hsguru_meta_wild_diamond_4to1",
        "https://www.hsguru.com/meta?format=1&min_games=100&rank=diamond_4to1",
        "hsguru",
        "meta",
        description="HSGuru meta, format=1, diamond 4-1 (min_games=100 for full table SSR).",
    ),
    Source(
        "hsguru_meta_standard_top_5k",
        "https://www.hsguru.com/meta?format=2&min_games=100&rank=top_5k",
        "hsguru",
        "meta",
        description="HSGuru meta, format=2, top 5k (min_games=100).",
    ),
    Source(
        "hsguru_meta_standard_top_legend",
        "https://www.hsguru.com/meta?format=2&min_games=100&rank=top_legend",
        "hsguru",
        "meta",
        description="HSGuru meta, format=2, top legend (min_games=100).",
    ),
    Source(
        "hsguru_meta_wild_top_legend",
        "https://www.hsguru.com/meta?format=1&min_games=100&rank=top_legend",
        "hsguru",
        "meta",
        description="HSGuru meta, format=1, min_games=100, top legend.",
    ),
    Source(
        "hsguru_meta_wild_top_5k",
        "https://www.hsguru.com/meta?format=1&min_games=100&rank=top_5k",
        "hsguru",
        "meta",
        description="HSGuru meta, format=1, min_games=100, top 5k.",
    ),
    Source(
        "hsguru_matchups_legend",
        "https://www.hsguru.com/matchups?min_archetype_sample=100&min_matchup_sample=25&rank=legend",
        "hsguru",
        "matchups",
        description="HSGuru Standard matchup matrix, legend, min archetype sample 100, min matchup sample 25.",
        stale_hours=36,
    ),
    Source(
        "hsguru_matchups_wild_legend",
        "https://www.hsguru.com/matchups?format=1&min_archetype_sample=100&min_matchup_sample=25&rank=legend",
        "hsguru",
        "matchups",
        description="HSGuru Wild matchup matrix, legend, min archetype sample 100, min matchup sample 25.",
        stale_hours=36,
    ),
    Source(
        "hsguru_matchups_diamond_4to1",
        "https://www.hsguru.com/matchups?rank=diamond_4to1",
        "hsguru",
        "matchups",
        description="HSGuru matchup matrix, diamond 4-1.",
    ),
    Source(
        "hsreplay_battlegrounds_comps",
        "https://hsreplay.net/battlegrounds/comps/",
        "hsreplay",
        "battlegrounds",
        description="HSReplay Battlegrounds comps.",
    ),
    Source(
        "hsreplay_battlegrounds_heroes",
        "https://hsreplay.net/battlegrounds/heroes/",
        "hsreplay",
        "battlegrounds",
        description="HSReplay premium Battlegrounds heroes tier list.",
    ),
    Source(
        "hsreplay_battlegrounds_minions",
        "https://hsreplay.net/battlegrounds/minions/#view=advanced",
        "hsreplay",
        "battlegrounds",
        description="HSReplay premium Battlegrounds minions advanced stats.",
    ),
    Source(
        "hsreplay_battlegrounds_compositions",
        "https://hsreplay.net/battlegrounds/compositions/",
        "hsreplay",
        "battlegrounds",
        description="HSReplay premium Battlegrounds compositions stats.",
    ),
    Source(
        "hsreplay_battlegrounds_trinkets_lesser",
        "https://hsreplay.net/api/v1/battlegrounds/trinkets/"
        "?format=json&hl=en&BattlegroundsMMRPercentile=TOP_1_PERCENT"
        "&BattlegroundsTimeRange=LAST_7_DAYS",
        "hsreplay",
        "battlegrounds",
        description="HSReplay lesser trinkets, Top 1% MMR, last 7 days.",
    ),
    Source(
        "hsreplay_battlegrounds_trinkets_greater",
        "https://hsreplay.net/api/v1/battlegrounds/trinkets/"
        "?format=json&hl=en&BattlegroundsMMRPercentile=TOP_1_PERCENT"
        "&BattlegroundsTimeRange=LAST_7_DAYS",
        "hsreplay",
        "battlegrounds",
        description="HSReplay greater trinkets, Top 1% MMR, last 7 days.",
    ),
    *tuple(
        Source(
            trinket_slice_source_id(mmr, time_range),
            "https://hsreplay.net/api/v1/battlegrounds/trinkets/"
            f"?format=json&hl=en&BattlegroundsMMRPercentile={mmr}"
            f"&BattlegroundsTimeRange={time_range}",
            "hsreplay",
            "battlegrounds",
            description=(
                "HSReplay lesser and greater trinkets, "
                f"{mmr}, {time_range}."
            ),
        )
        for mmr, time_range in TRINKET_SLICE_PARAMETERS
    ),
    Source(
        "hsreplay_arena",
        "https://hsreplay.net/arena/",
        "hsreplay",
        "arena",
        description="HSReplay Arena overview.",
    ),
    Source(
        "hsreplay_arena_legendaries",
        "https://hsreplay.net/arena/legendaries/",
        "hsreplay",
        "arena",
        description="HSReplay Arena legendaries.",
    ),
    Source(
        "hsreplay_arena_winning_decks",
        "https://hsreplay.net/arena/winning_decks/#playerClass=ALL",
        "hsreplay",
        "arena",
        description="HSReplay Arena winning decks. Fragment is client-side.",
    ),
    Source(
        "hsreplay_arena_cards_advanced",
        "https://hsreplay.net/arena/cards/#view=advanced",
        "hsreplay",
        "arena",
        description="HSReplay Arena cards. Fragment is client-side.",
    ),
    Source(
        "hsreplay_decks_trending",
        "https://hsreplay.net/decks/trending/",
        "hsreplay",
        "ranked",
        description="HSReplay trending decks.",
    ),
    Source(
        "hsreplay_cards_legend_included_winrate",
        "https://hsreplay.net/cards/#rankRange=GOLD&sortBy=includedWinrate&timeRange=LAST_14_DAYS",
        "hsreplay",
        "ranked",
        description="HSReplay cards, Gold rank, 14 days, sorted by included winrate.",
    ),
    Source(
        "hsreplay_cards_legend_included_popularity",
        "https://hsreplay.net/cards/#rankRange=GOLD&sortBy=includedPopularity&timeRange=LAST_14_DAYS",
        "hsreplay",
        "ranked",
        description="HSReplay cards, Gold rank, 14 days, sorted by included popularity.",
    ),
    *tuple(
        Source(
            spec.source_id,
            spec.url,
            "hsreplay",
            "ranked",
            description=spec.description,
        )
        for spec in HSREPLAY_CARD_PERIOD_SOURCE_SPECS
    ),
    Source(
        "hsreplay_meta_archetypes_legend_eu_1d",
        "https://hsreplay.net/meta/#rankRange=LEGEND&tab=archetypes&region=REGION_EU&timeFrame=LAST_1_DAY&popularitySortBy=rank51",
        "hsreplay",
        "ranked",
        description="HSReplay meta archetypes grouped by class, Legend EU, last 1 day.",
    ),
    Source(
        "hsreplay_meta_top_1000_legend_1d_firecrawl",
        "https://hsreplay.net/meta/#rankRange=TOP_1000_LEGEND&timeFrame=LAST_1_DAY",
        "hsreplay",
        "ranked",
        description="HSReplay meta archetypes, Top 1000 Legend, last 1 day, Firecrawl page scrape + analytics.",
    ),
    Source(
        "hsreplay_meta_legend_1d_firecrawl",
        "https://hsreplay.net/meta/#rankRange=LEGEND&timeFrame=LAST_1_DAY",
        "hsreplay",
        "ranked",
        description="HSReplay meta archetypes, Legend, last 1 day, Firecrawl page scrape + analytics.",
    ),
    Source(
        "hsreplay_meta_diamond_4to1_1d_firecrawl",
        "https://hsreplay.net/meta/#rankRange=DIAMOND_FOUR_THROUGH_DIAMOND_ONE&timeFrame=LAST_1_DAY",
        "hsreplay",
        "ranked",
        description="HSReplay meta archetypes, Diamond 4-1, last 1 day, Firecrawl page scrape + analytics.",
    ),
    Source(
        "hsreplay_arena_class_pages_firecrawl",
        "https://hsreplay.net/arena/deathknight/",
        "hsreplay",
        "arena",
        description="HSReplay Arena class pages via Firecrawl, with class winrate, 7+ wins, pick rate and runs.",
    ),
    Source(
        "firestone_battlegrounds_comps",
        "https://www.firestoneapp.com/battlegrounds/comps",
        "firestone",
        "battlegrounds",
        description="Firestone Battlegrounds compositions.",
    ),
    Source(
        "firestone_battlegrounds_cards",
        "https://www.firestoneapp.com/battlegrounds/cards?time=past-three&tavernTiers=1,7,2,3,4,5,6&turns=10",
        "firestone",
        "battlegrounds",
        description="Firestone Battlegrounds minion/card statistics by tavern tier.",
    ),
    Source(
        "firestone_battlegrounds_spells",
        "https://www.firestoneapp.com/battlegrounds/cards?time=past-three&tavernTiers=1,7,2,3,4,5,6&turns=10&type=spell",
        "firestone",
        "battlegrounds",
        description="Firestone Battlegrounds spell statistics by tavern tier.",
    ),
    Source(
        "firestone_arena_cards_normal",
        "https://www.firestoneapp.com/arena/cards?arenaActiveTimeFilter=past-three&arenaActiveMode=arena",
        "firestone",
        "arena",
        description="Firestone Regular Arena card stats.",
    ),
    Source(
        "firestone_arena_cards_underground",
        "https://www.firestoneapp.com/arena/cards?arenaActiveTimeFilter=past-three",
        "firestone",
        "arena",
        description="Firestone Underground Arena card stats.",
    ),
    Source(
        "firestone_arena_legendaries_underground",
        "https://www.firestoneapp.com/arena/cards?arenaActiveTimeFilter=past-three&arenaActiveCardTypeFilter=legendary",
        "firestone",
        "arena",
        description="Firestone Underground Arena legendary card stats.",
    ),
    Source(
        "firestone_arena_legendaries_normal",
        "https://www.firestoneapp.com/arena/cards?arenaActiveTimeFilter=past-three&arenaActiveCardTypeFilter=legendary&arenaActiveMode=arena",
        "firestone",
        "arena",
        description="Firestone Regular Arena legendary card stats.",
    ),
    Source(
        "heartharena_tierlist",
        "https://www.heartharena.com/ru/tierlist",
        "heartharena",
        "arena",
        description="HearthArena card tier-list.",
    ),
    Source(
        "metastats_decks",
        "https://metastats.net/hearthstone/class/decks/DeathKnight/",
        "metastats",
        "ranked",
        description="MetaStats archetypes and decks for all classes.",
    ),
    Source(
        "metastats_matchups",
        "https://metastats.net/hearthstone/archetype/matchup/",
        "metastats",
        "matchups",
        description="MetaStats archetype matchups.",
    ),
    Source(
        "hearthstone_decks",
        "https://hearthstone-decks.net/standard-decks/",
        "hearthstone-decks",
        "ranked",
        description="Top 500 Legend Standard and Wild Decks from hearthstone-decks.net.",
    ),
    Source(
        "vicious_syndicate_radars",
        "https://www.vicioussyndicate.com/deck-library/death-knight-decks/",
        "vicious-syndicate",
        "matchups",
        description="Vicious Syndicate Data Reaper's Radars (network graph of cards).",
    ),
    Source(
        "vicious_syndicate_live_beta",
        "https://www.vicioussyndicate.com/data-reaper-live-beta/",
        "vicious-syndicate",
        "meta",
        description="Vicious Syndicate Data Reaper Live class/deck distribution and power tier list.",
    ),
    # --- Pipeline sources (kind="pipeline") -----------------------------------
    # Refreshed by dedicated systemd timers, NOT by the generic scrape planner
    # (fetcher tiers). stale_hours = timer period + ~24h slack.
    Source(
        id="hsguru_meta_matrix",
        url="https://www.hsguru.com/meta",
        site="hsguru",
        category="meta_matrix",
        description=(
            "Unified Standard/Wild HSGuru matrix refreshed every twelve hours through Firecrawl: "
            "nine ranks (including ALL, Top-500 and Top-100), five rolling periods, Any Player, "
            "six locally derived min-games filters, plus the current-patch 50-game archetype "
            "catalog with cached HSGuru builds and history."
        ),
        stale_hours=36,
        kind="pipeline",
    ),
    Source(
        id="hsreplay_battlegrounds_hero_details",
        url="https://hsreplay.net/battlegrounds/heroes/",
        site="hsreplay",
        category="battlegrounds",
        description=(
            "BG hero detail cache built twice weekly by the systemd timer "
            "hs-data-api-docker-refresh-bg-hero-details.timer (Mon,Thu 04:35 Europe/Warsaw); "
            "stale_hours = 96h maximum gap + 24h slack."
        ),
        stale_hours=120,
        kind="pipeline",
    ),
    Source(
        id="hsreplay_archetypes",
        url="https://hsreplay.net/meta/",
        site="hsreplay",
        category="meta",
        description=(
            "HSReplay Standard archetype SQLite database built twice per week by the "
            "systemd timer hs-data-api-docker-refresh-hsreplay-archetypes.timer "
            "(Mon,Thu 03:20 Europe/Warsaw); stale_hours = 96h max gap + 24h slack."
        ),
        stale_hours=120,
        kind="pipeline",
    ),
    Source(
        id="hsguru_fun_decks",
        url="https://www.hsguru.com/streamer-decks",
        site="hsguru",
        category="fun_decks",
        description=(
            "Off-meta / fun decks derived from streamer-deck candidates by low overlap "
            "with HSGuru meta catalogs and niche archetype popularity. "
            "Refreshed after streamer Firecrawl runs, plus a dedicated Standard-focused refresh every 2 hours (refresh-fun-decks --format standard)."
        ),
        stale_hours=36,
        kind="pipeline",
    ),
)


SOURCE_BY_ID = {source.id: source for source in SOURCES}
