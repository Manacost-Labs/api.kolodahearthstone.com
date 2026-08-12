from __future__ import annotations

from typing import Any


def _try_import(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def _stack_rows() -> list[dict[str, Any]]:
    from .config import fetch_backends, fetch_playwright_stealth_enabled

    backends = ", ".join(fetch_backends())
    stealth_on = fetch_playwright_stealth_enabled()

    def row(
        name: str,
        role: str,
        layer: str,
        status: str,
        *,
        link: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "name": name,
            "role": role,
            "layer": layer,
            "status": status,
        }
        if link:
            item["link"] = link
        if notes:
            item["notes"] = notes
        return item

    rows: list[dict[str, Any]] = [
        row(
            "FastAPI + Uvicorn",
            "HTTP API и демо UI",
            "api",
            "production",
            link="https://fastapi.tiangolo.com/",
        ),
        row(
            "nginx",
            "TLS и reverse proxy → api.kolodahearthstone.com",
            "infra",
            "production",
            link="https://nginx.org/",
        ),
        row(
            "SQLite / JSON cache",
            "Кэш датасетов и статусов источников",
            "storage",
            "production",
        ),
        row(
            "systemd timers",
            "Плановый refresh и stale-monitor",
            "ops",
            "production",
        ),
        row(
            "FlareSolverr",
            "Обход Cloudflare для HSGuru / HSReplay",
            "fetch",
            "production",
            link="https://github.com/FlareSolverr/FlareSolverr",
            notes="В настроенной цепочке fetch backends",
        ),
        row(
            "Scrapling (StealthyFetcher)",
            "Headless браузер с CF solver",
            "fetch",
            "production" if _try_import("scrapling") else "optional",
            link="https://github.com/D4Vinci/Scrapling",
        ),
        row(
            "Patchright",
            "Playwright-fork для защищённых страниц",
            "fetch",
            "production" if _try_import("patchright") else "optional",
            link="https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python",
            notes=f"stealth={'on' if stealth_on else 'off'}",
        ),
        row(
            "playwright-stealth",
            "Скрытие automation-сигналов на Patchright",
            "fetch",
            "production"
            if stealth_on and _try_import("playwright_stealth")
            else "optional",
            link="https://github.com/AtuboDad/playwright_stealth",
        ),
        row(
            "curl-cffi",
            "TLS impersonation; HSReplay GraphQL POST",
            "fetch",
            "production" if _try_import("curl_cffi") else "optional",
            link="https://github.com/lexiforest/curl_cffi",
            notes="TLS/browser impersonation для API-first источников",
        ),
        row(
            "CloakBrowser",
            "Альтернативный stealth Chromium (lab/HSGuru)",
            "fetch",
            "lab" if _try_import("cloakbrowser") else "optional",
            link="https://github.com/chromedp/cloakbrowser",
        ),
        row(
            "cloudscraper / httpx",
            "Fallback HTTP для открытых API",
            "fetch",
            "production",
        ),
        row(
            "Residential proxy",
            "Прокси для 403/429 на защищённых сайтах",
            "network",
            "production",
            notes="Используется только для источников, где это разрешено tier policy",
        ),
        row(
            "Premium session storage",
            "Локальная серверная сессия для premium источников",
            "auth",
            "production",
            notes="Файлы сессий хранятся вне git с ограниченными правами",
        ),
        row(
            "mitmproxy",
            "Перехват трафика для поиска GraphQL/API",
            "discovery",
            "lab",
            link="https://github.com/mitmproxy/mitmproxy",
            notes="scripts/mitmproxy_graphql_capture.py",
        ),
        row(
            "Scrapy + scrapy-playwright",
            "Экспериментальные пауки (не в cron)",
            "discovery",
            "lab",
            link="https://github.com/scrapy-plugins/scrapy-playwright",
            notes="lab/scrapy_hsreplay/",
        ),
        row(
            "HearthstoneJSON",
            "Карты и метаданные колод",
            "data",
            "production",
            link="https://hearthstonejson.com/",
        ),
    ]
    rows.append(
        row(
            "Активные backends",
            backends or "—",
            "config",
            "production",
            notes="Текущая настроенная цепочка fetch backends",
        )
    )
    return rows


def _site_cards() -> list[dict[str, Any]]:
    def site(
        key: str,
        name: str,
        homepage: str,
        role: str,
        *,
        frontend: list[str],
        backend: list[str],
        hosting: list[str],
        apis: list[dict[str, Any]],
        parser_strategy: list[str],
        auth: str,
        risks: list[str],
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "name": name,
            "homepage": homepage,
            "role": role,
            "frontend": frontend,
            "backend": backend,
            "hosting": hosting,
            "apis": apis,
            "parser_strategy": parser_strategy,
            "auth": auth,
            "risks": risks,
            "notes": notes or [],
        }

    return [
        site(
            "hsguru",
            "HSGuru",
            "https://www.hsguru.com/",
            "Основной источник Standard/Wild меты, матчапов и streamer decks.",
            frontend=[
                "React/Next-style SPA/SSR интерфейс с клиентскими фильтрами меты.",
                "Таблицы меты и матчапов доступны как HTML/JS-rendered content.",
                "Сильно зависит от Cloudflare/browser fingerprint на защищённых маршрутах.",
            ],
            backend=[
                "Публичная кухня API не документирована; используем страницы как источник истины.",
                "Данные меты приходят в SSR/HTML и/или клиентские JSON-фрагменты, завязанные на query-параметры format/rank.",
                "Матчапы и meta pages чувствительны к структуре таблиц и клиентскому рендеру.",
            ],
            hosting=[
                "Публичный домен за Cloudflare/anti-bot слоем.",
                "У нас проходит через browser_protected tier: FlareSolverr/Patchright fallback, residential proxy при необходимости.",
            ],
            apis=[
                {
                    "name": "Meta pages",
                    "url_pattern": "https://www.hsguru.com/meta?format={format}&rank={rank}&min_games=100",
                    "type": "html/ssr",
                    "used_for": "Архетипы, winrate, popularity, games по рангам.",
                },
                {
                    "name": "Matchups pages",
                    "url_pattern": "https://www.hsguru.com/matchups?rank={rank}",
                    "type": "html/ssr",
                    "used_for": "Матрица матчапов.",
                },
            ],
            parser_strategy=[
                "Сначала browser/fetch pipeline получает HTML.",
                "Дальше parser извлекает таблицы и нормализует их в structured meta/matchups.",
                "Quality gate проверяет минимальный размер таблицы и наличие ожидаемого контента.",
            ],
            auth="Публичные страницы, но anti-bot может требовать браузерный backend и proxy.",
            risks=[
                "Cloudflare/403/429 и fingerprint drift.",
                "Изменение DOM/таблиц ломает HTML parser.",
                "Данные могут быть частично отрендерены клиентом, поэтому pure HTTP fallback ненадёжен.",
            ],
        ),
        site(
            "hsreplay",
            "HSReplay",
            "https://hsreplay.net/",
            "Premium и public источник карт, арены, Battlegrounds и ranked meta.",
            frontend=[
                "Webpack React app; страницы содержат react_context/userdata и загружают chunk bundles.",
                "Фильтры часто живут в URL fragment (#rankRange=..., #view=advanced), но реальные данные берутся из API.",
                "Часть колонок скрывается в UI, но уже присутствует в analytics payload.",
            ],
            backend=[
                "Django/DRF style endpoints встречаются в /api/v1/..., часть JSON иногда возвращается HTML-wrapped в <pre>.",
                "Основная аналитика идёт через /analytics/query/{key}/ и /api/v1/*.",
                "Premium доступ определяется серверной сессией; Battlegrounds/advanced views требуют подписку.",
            ],
            hosting=[
                "hsreplay.net за Cloudflare/anti-bot.",
                "Статика Webpack раздаётся через static.hsreplay.net.",
                "Для premium API используем browser-backed fetch с сохранённой серверной сессией; часть analytics вызовов идёт через HSReplay client.",
            ],
            apis=[
                {
                    "name": "Ranked cards",
                    "url_pattern": "https://hsreplay.net/analytics/query/card_list/?GameType={game_type}&TimeRange={time_range}&LeagueRankRange={rank}",
                    "type": "analytics json",
                    "used_for": "Карты Standard/Wild: popularity, copies, deck winrate, played/drawn/keep/turn columns.",
                },
                {
                    "name": "Meta archetypes",
                    "url_pattern": "https://hsreplay.net/analytics/query/archetype_popularity_distribution_stats_v2/?GameType=RANKED_STANDARD&LeagueRankRange=LEGEND&Region=REGION_EU&TimeRange=LAST_1_DAY",
                    "type": "analytics json",
                    "used_for": "Разбивка по классам и архетипам: winrate, popularity, games.",
                },
                {
                    "name": "Arena cards advanced",
                    "url_pattern": "https://hsreplay.net/analytics/query/arena_card_stats/",
                    "type": "analytics json",
                    "used_for": "Arenasmith score, deck/drawn/played winrate, runs, copies, games.",
                },
                {
                    "name": "Battlegrounds minions/compositions",
                    "url_pattern": "https://hsreplay.net/analytics/query/battlegrounds_{minion_list|comp_stats}/",
                    "type": "analytics json",
                    "used_for": "BG minions impact/win share/popularity и compositions placement distribution.",
                },
                {
                    "name": "Battlegrounds reference APIs",
                    "url_pattern": "https://hsreplay.net/api/v1/battlegrounds/{heroes|compositions}/?hl=en",
                    "type": "DRF json/html-pre",
                    "used_for": "Имена, dbfId, hero placement distribution и composition names.",
                },
            ],
            parser_strategy=[
                "Предпочитаем API-first: analytics JSON вместо DOM.",
                "Cookies хранятся локально с 0600 и используются только fetcher layer.",
                "Parser нормализует payload aliases, считает производные поля и добавляет HearthstoneJSON card metadata.",
                "Quality gates проверяют количество карт/героев/композиций и заполненность метрик.",
            ],
            auth="Часть источников public, premium источники используют локальную серверную сессию; секреты не возвращаются в API/UI.",
            risks=[
                "Истечение premium-сессии или entitlement.",
                "Смена analytics key/полей payload.",
                "DRF endpoints иногда возвращают JSON внутри HTML <pre>, нужен tolerant parser.",
                "Fragment URL не отправляется на сервер; parser обязан сам строить API query.",
            ],
        ),
        site(
            "firestone",
            "Firestone",
            "https://www.firestoneapp.com/",
            "Battlegrounds, Arena и Standard-статистика из JSON/CDN источников Firestone.",
            frontend=[
                "Modern SPA на firestoneapp.com.",
                "UI фильтры соответствуют query parameters: tavern tiers, turns, arena mode/time filters.",
            ],
            backend=[
                "Используются JSON/API endpoints и static.zerotoheroes.com CDN snapshots.",
                "Данные уже агрегированы: Standard decks/archetypes, cards, comps, Arena и BG.",
            ],
            hosting=[
                "HTTPS/API не требует premium session; это не заменяет разрешение на использование данных.",
                "В parser tier это light_api/firestone_api; Standard делает два direct CDN GET без платных scrape-провайдеров и residential proxy.",
            ],
            apis=[
                {
                    "name": "Standard meta overviews",
                    "url_pattern": "https://static.zerotoheroes.com/api/constructed/stats/{decks|archetypes}/standard/legend/last-patch/overview-from-hourly.gz.json",
                    "type": "gzip/json CDN",
                    "used_for": "Колоды и архетипы Standard Legend за последний патч; winrate — доля 0..1.",
                },
                {
                    "name": "Battlegrounds cards/spells",
                    "url_pattern": "https://www.firestoneapp.com/battlegrounds/cards?time=past-three&tavernTiers=...&turns=10&type={spell?}",
                    "type": "json/api",
                    "used_for": "BG карты по тавернам, spells, average placement, impact.",
                },
                {
                    "name": "Battlegrounds comps",
                    "url_pattern": "https://www.firestoneapp.com/battlegrounds/comps",
                    "type": "json/api/html-backed",
                    "used_for": "Композиции и ключевые карты.",
                },
                {
                    "name": "Arena cards",
                    "url_pattern": "https://www.firestoneapp.com/arena/cards?arenaActiveTimeFilter=past-three&arenaActiveMode={arena}",
                    "type": "json/api",
                    "used_for": "Обычная и Underground Arena статистика карт/легендарок.",
                },
            ],
            parser_strategy=[
                "API-first через firestone_api.",
                "Standard-набор параллельно загружает два CDN-среза и требует минимум 10 decks + 10 archetypes.",
                "Нормализация карт через HearthstoneJSON.",
                "Quality gates проверяют схему, семантику, объём, deck codes и регрессию до публикации; при отказе остаётся LKG.",
            ],
            auth="Серверная сессия не требуется. Публичное/коммерческое включение firestone_standard требует письменного разрешения Firestone/ZeroToHeroes.",
            risks=[
                "Изменение query parameters или структуры app JSON.",
                "Семантика period filters может отличаться от HSReplay.",
                "Firestone Terms of Service: https://github.com/Zero-to-Heroes/firestone/blob/master/tos.md; не включать firestone_standard публично/коммерчески без письменного разрешения.",
            ],
        ),
        site(
            "heartharena",
            "HearthArena",
            "https://www.heartharena.com/",
            "Тир-листы карт арены.",
            frontend=[
                "Классический web UI с таблицами/карточками tier list.",
                "Данные ориентированы на class-specific arena ratings.",
            ],
            backend=[
                "Публичный HTML/API surface без документированного официального API.",
                "Parser извлекает классы, карты, score/tier labels.",
            ],
            hosting=[
                "Публичный HTTPS сайт; обычно доступен direct/light API pipeline.",
            ],
            apis=[
                {
                    "name": "Arena tier list",
                    "url_pattern": "https://www.heartharena.com/tierlist",
                    "type": "html/api-derived",
                    "used_for": "Оценка карт, tier names, классы.",
                }
            ],
            parser_strategy=[
                "Получаем страницу/API, группируем по классам.",
                "Сверяем total_cards и cards with tier_id в quality gate.",
            ],
            auth="Публичный доступ.",
            risks=[
                "Нестабильная HTML-разметка.",
                "Обновления арены могут временно менять состав классов/карт.",
            ],
        ),
        site(
            "metastats",
            "MetaStats",
            "https://metastats.net/",
            "Альтернативный источник ranked decks и matchup matrix.",
            frontend=[
                "Web UI поверх открытых JSON endpoints.",
                "Страницы используют данные архетипов/колод с deck codes.",
            ],
            backend=[
                "Открытые API endpoints для decks и matchups.",
                "Данные включают archetype/class, games, winrate и matchup cells.",
            ],
            hosting=[
                "Публичный HTTPS API, в нашем tier light_api/metastats_api.",
            ],
            apis=[
                {
                    "name": "Decks",
                    "url_pattern": "MetaStats decks API",
                    "type": "json/api",
                    "used_for": "Архетипы, версии колод, deck codes, winrate/games.",
                },
                {
                    "name": "Matchups",
                    "url_pattern": "MetaStats matchups API",
                    "type": "json/api",
                    "used_for": "Матрица matchup winrates.",
                },
            ],
            parser_strategy=[
                "API-first, затем декод deck codes и группировка по классам/архетипам.",
                "Quality gate проверяет минимальное число decks/matchups.",
            ],
            auth="Публичный доступ.",
            risks=[
                "Неофициальные endpoints могут менять имена полей.",
                "Deck codes могут отсутствовать для части версий.",
            ],
        ),
        site(
            "hearthstone-decks",
            "Hearthstone-Decks",
            "https://hearthstone-decks.net/",
            "Топ легенда Standard/Wild decklists.",
            frontend=[
                "WordPress/PHP-like контентный сайт с постами и таблицами decklists.",
                "Deck codes и ранги доступны в WordPress content JSON; HTML остаётся fallback.",
            ],
            backend=[
                "Первичный маршрут — WordPress REST API `wp-json/wp/v2/posts`.",
                "Parser валидирует post ID, URL, category, timestamps, content и декодирует deck code.",
            ],
            hosting=[
                "Два WordPress REST GET идут напрямую; платные маршруты возможны только для валидированного HTML/detail fallback.",
            ],
            apis=[
                {
                    "name": "WordPress REST posts",
                    "url_pattern": "https://hearthstone-decks.net/wp-json/wp/v2/posts?categories={3|13}&per_page=20&page=1&orderby=date&order=desc",
                    "type": "wordpress/json",
                    "used_for": "20 Standard + 20 Wild posts, deck codes, player/rank и published/modified timestamps.",
                },
                {
                    "name": "Validated HTML fallback",
                    "url_pattern": "https://hearthstone-decks.net/{standard-decks|wild-decks}/",
                    "type": "html/cms",
                    "used_for": "Аварийный список и detail pages, если REST-набор отклонён.",
                },
            ],
            parser_strategy=[
                "Два параллельных REST GET: category 3 Standard и category 13 Wild, по 20 постов.",
                "Декод deck codes, точечное дополнение из LKG/detail page и группировка Standard/Wild.",
                "Контракт требует 40 строк и 95% deck codes; иначе остаётся LKG.",
            ],
            auth="Публичный доступ.",
            risks=[
                "Изменение WordPress REST category ID, schema или CMS markup fallback.",
                "Посты могут содержать неполные или устаревшие deck codes.",
            ],
        ),
        site(
            "vicious-syndicate",
            "Vicious Syndicate",
            "https://www.vicioussyndicate.com/",
            "Data Reaper Live и радары карт/синергий.",
            frontend=[
                "WordPress + MemberPress для аккаунтов/Gold access.",
                "Data Reaper Live — отдельное JS приложение с Firebase SDK и Plotly.",
                "Premium page подключает build_premium.js, basic page подключает build_basic.js.",
            ],
            backend=[
                "Data Reaper Live читает Firebase Realtime Database.",
                "Firebase paths: data/ladderData, data/tableData, premiumData/ladderData, premiumData/tableData.",
                "Gold Live использует клиентскую Firebase-авторизацию, доступную только через premium-приложение.",
                "Deck library/radar pages парсятся отдельно из сайта VS.",
            ],
            hosting=[
                "WordPress сайт за Cloudflare/WordPress stack.",
                "Firebase Realtime Database: https://data-reaper.firebaseio.com.",
                "WordPress/MemberPress управляет доступом к premium странице; значения сессий не сохраняются в публичных API ответах.",
            ],
            apis=[
                {
                    "name": "Firebase ladderData",
                    "url_pattern": "https://data-reaper.firebaseio.com/premiumData/ladderData/{Standard|Wild}.json",
                    "type": "firebase json",
                    "used_for": "Pie chart class/deck distribution.",
                },
                {
                    "name": "Firebase tableData",
                    "url_pattern": "https://data-reaper.firebaseio.com/premiumData/tableData/{Standard|Wild}.json",
                    "type": "firebase json",
                    "used_for": "Power tier list через matchup matrix.",
                },
                {
                    "name": "Deck library radars",
                    "url_pattern": "https://www.vicioussyndicate.com/deck-library/{class}-decks/",
                    "type": "html/js data",
                    "used_for": "Card synergy radar nodes/edges.",
                },
            ],
            parser_strategy=[
                "Для Live: пройти серверную Firebase-авторизацию, скачать premium JSON, воспроизвести формулы сайта.",
                "Для tier-list: lastDay ladder frequency + last2Weeks matchup table.",
                "Ошибки внешних API санитизируются, чтобы чувствительные параметры не попадали в logs/status.",
            ],
            auth="Gold Live требует WordPress/MemberPress доступ к premium page; временные auth credentials используются только на backend и не показываются в UI.",
            risks=[
                "Смена Firebase rules/build_premium.js.",
                "Истечение WordPress/Gold-сессии или entitlement.",
                "Смена формул PowerWindow в JS приложении.",
            ],
        ),
        site(
            "hearthstonejson",
            "HearthstoneJSON",
            "https://hearthstonejson.com/",
            "Справочник карт и локализаций для нормализации всех источников.",
            frontend=[
                "Статические JSON файлы по локалям и версиям.",
            ],
            backend=[
                "Файловые JSON dumps: cards, dbfId, card id, ruRU/enUS names, mana/type/rarity.",
            ],
            hosting=[
                "Публичный CDN/static hosting.",
                "Локальный cache хранится в data dir как card metadata cache.",
            ],
            apis=[
                {
                    "name": "Cards JSON",
                    "url_pattern": "https://api.hearthstonejson.com/v1/latest/{locale}/cards.collectible.json",
                    "type": "static json",
                    "used_for": "card_id/dbfId/name lookup, русские названия, card metadata.",
                }
            ],
            parser_strategy=[
                "Используем как enrichment layer, не как игровой статистический источник.",
                "По dbfId/card id добавляем имя, mana, type и локализацию.",
            ],
            auth="Публичный доступ.",
            risks=[
                "Задержка обновления после патча Hearthstone.",
                "Неколлекционные/специальные BG карты могут отсутствовать в collectible dump.",
            ],
        ),
    ]


def build_technologies_payload() -> dict[str, Any]:
    from .config import fetch_playwright_stealth_enabled

    rows = _stack_rows()
    sites = _site_cards()
    return {
        "title": "Hearthstone Parses — технологический стек",
        "updated": "2026-06-07",
        "playwright_stealth_enabled": fetch_playwright_stealth_enabled(),
        "playwright_stealth_installed": _try_import("playwright_stealth"),
        "technologies": rows,
        "sites": sites,
        "count": len(rows),
        "site_count": len(sites),
    }
