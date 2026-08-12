# Каталог данных

## Constructed

- статистика Standard/Wild карт по рангу и периоду;
- HSGuru meta и matchup matrices;
- HSReplay архетипы, mulligan, matchups, decks и history;
- Firestone Standard decks/archetypes;
- Hearthstone‑Decks Standard/Wild posts и deck codes;
- fun/off-meta и streamer decks.

## Battlegrounds

- герои и Duos, portraits, powers, buddies, tavern-up/best composition;
- существа/карты по tier, MMR и периоду;
- combat rounds и history;
- compositions и validated screenshot;
- trinkets, spells и специальные коллекции.

## Arena

- обычная и Underground Arena;
- статистика карт;
- классы;
- легендарные группы;
- winning decks;
- HearthArena tier list.

## Системные данные

- реестр источников и freshness policy;
- dataset version/size/state;
- parser reliability windows;
- patches;
- job/trace/operator events.

## Как найти endpoint

1. Для human browsing откройте модуль панели.
2. Для готовой интеграции ищите типизированный `/v1/*` endpoint.
3. Для полного parser payload используйте `/datasets/{source_id}`.
4. Для связанных полей используйте typed GraphQL query.
5. Для произвольной central table используйте `collections`/`records`.

## Source states

| State | Данные |
| --- | --- |
| Fresh | Новый проверенный snapshot |
| Provisional | Ранний post-patch snapshot |
| LKG | Предыдущий хороший snapshot после новой ошибки |
| Stale | Snapshot существует, но старше policy |
| Failed | Нет подходящего snapshot |

Полный и актуальный справочник:

- [DATA_CATALOG.md](../docs/DATA_CATALOG.md) — поля и примеры;
- [SOURCES.md](../docs/SOURCES.md) — все 99 source ID, генерируются из кода.
