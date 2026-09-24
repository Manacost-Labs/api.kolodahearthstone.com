# Constructed card reveals

The `kolodahs-sync-constructed-cards.timer` already runs `sync_constructed_cards.py` every four hours. The normal Blizzard Game Data API `set=standard` response is the authority for playable Standard cards. Before the 2026 “Reign of the Black Empire” release, the official [card gallery](https://hearthstone.blizzard.com/en-us/cards?set=reign-of-the-black-empire) exposes revealed cards that may not yet be in that set group. The importer reads the gallery's public `/en-us/api/cards` and `/ru-ru/api/cards` JSON for that one expansion while syncing both formats, because Standard and Wild share card records. Gallery-only cards are added to Standard; a Wild run enriches only cards already in its Game Data set. This is a direct JSON source, so the protected-page scrape provider cascade is not involved.

Only collectible cards from expansion set ID `1994` with a stable DBF and at least one public name are imported. A missing locale, image, rules field, type or rarity is allowed. Missing optional values preserve previously imported preview values. Blizzard Game Data may already include the DBF in `set=standard` without expansion membership. In that case the official gallery supplies set `BE` and the format row remains `availability_status=preview`; public Game Data fields fill gallery gaps. The API includes previews in the library because `in_format=1`. Once Game Data assigns expansion set ID `1994`, the normal record wins and availability becomes `available`; official gallery fields, including art, fill its remaining gaps. Existing `blizzard:<dbf>` IDs can migrate to canonical HearthstoneJSON IDs through the importer's established path.

HearthstoneJSON may supply a matching canonical card ID, but its names, rules and images are not copied into a preview record before Blizzard publishes them. Gallery image URLs are accepted only from the observed Blizzard image hosts. An internal `~GAMEPLAY ASPROXY ...` suffix in gallery rules text is removed before publication.

The gallery JSON endpoint is used by Blizzard's site, but is not part of the documented Game Data API. Treat it as an observed source contract. Both locales are validated for pagination, unique positive IDs, collectibility and exact set membership. One locale may fail while the other is healthy; if both fail, the run fails before changing format membership. The removal pass excludes preview rows so an incomplete gallery response cannot silently erase earlier reveals. An endpoint or response-shape change requires a fixture update and manual source check, not relaxed validation.

## Checks

- Run `.venv/bin/python -m unittest panel.tests.test_sync_constructed_cards -v`.
- Run `make check` and `make security` before publication.
- Compare the gallery's `cardCount` in both locales with the importer's `preview` and `preview_locale_errors` run counters. A lower preview count is expected when cards have joined the normal Standard set.
- After an authorized deployment, inspect a new card in `/api/v1/constructed-cards?format=standard&set=BE&collectible=1` and its `/standard/cards/standard/<card_id>/` page. Check the next timer run and the last import run without printing credentials.

If the source fails, preserve the previous preview rows and investigate the gallery response. Do not fill gaps from the entire HearthstoneJSON catalog: it includes hidden, internal and retired cards.
