# HS Data admin UI

## Purpose

`api.kolodahearthstone.com` is an operational catalogue for Hearthstone data. Its UI is
optimized for fast scanning and maintenance rather than for public browsing.
The interface is the Next.js application in [`panel-next`](../../panel-next/README.md).
The former server-rendered PHP view (`index.php`, `partials/`, its CSS and DOM
scripts) has been retired; PHP now provides only authentication, the JSON
adapters behind `/api/panel` and the public REST API.

The trinket catalogue has a dedicated **Full art** column. The import and API
contracts are documented in `LIBRARY_FULL_ART.md`.

## Source map

- `next-data.php` + `lib/panel_data.php` + `lib/next_panel.php` — the fixed
  action allowlist, catalogue queries, filter state, validation and mutation
  budgets behind `/api/panel`. They return only the explicit JSON contract.
- `analytics.php` + `lib/analytics.php` — protected, read-only allowlist gateway
  to the local Hearthstone Data API. New statistics modules are registered in
  `analytics_module_registry()` and normalized before reaching the browser.
- `parser-control.php` + `lib/parser_control.php` — narrow authenticated bridge
  for the local parser control API. The browser never receives its admin token.
- `assets/parsing-reliability.js` and `assets/parser-control-view.js` — pure,
  dependency-free view models. `panel-next` copies them into `lib/vendor/`
  during `prebuild`; the same logic is exercised in Node tests.
- `auth/` + `lib/auth.php` — GitHub sign-in limited to `Zulut30`. A retired
  `/index.php?…` return address is rewritten to the Next.js root `/?…`.
- `api/index.php` — public API; it is not coupled to the admin presentation.

These three PHP adapters are reachable only through the private
`127.0.0.1:18769` listener used by the Next.js server.

## Interaction contracts

- Filters use URL query parameters and stay shareable/bookmarkable.
- Golden variants are represented inside their base-card row, but their IDs and
  DBFs are also searchable.
- The parser workspace prioritizes error/partial sources, keeps source and
  action columns sticky, and exposes schedules, published row counts and recent
  run progress without loading raw operational logs into the initial view.
- Manual parser runs require GitHub authentication, a same-origin CSRF token,
  an application-level rate budget and an explicit confirmation dialog.
- The “Обзор и мета” workspace opens with the complete source registry. It shows
  the effective state, dataset availability, last update and calculated age for
  every source returned by `/demo/overview`.
- The overview loads `/v1/system/parsing-reliability` independently. Its primary
  percentage is full fresh publication; data availability (including LKG) and
  accepted freshness are separate metrics. Provisional, LKG, failed and timed
  out outcomes remain visible as counts. Internally consistent `collecting`
  windows are visible as an explicitly preliminary slice; only a complete
  `observed` window is labelled as observed. The default view is the latest 24h
  window. The current scope covers observed scraper and dedicated-pipeline
  attempts, but missing scheduled pipeline windows remain undetectable until the
  schedule ledger is complete. Missing, malformed, inconsistent, or legacy
  estimated telemetry renders as “Накапливаем статистику” and never falls back
  to a synthetic 100%.
- Every reliability window also shows verified extraction completeness as a
  separate SLO: fresh responses normalized without unexplained loss, the 99%
  target, instrumented/catalog source rollout, observed instrumented cohort,
  tracked-attempt coverage, and complete/incomplete/unknown states. The weighted
  attempt rate is separate from source-target attainment, the unweighted macro
  rate (unobserved instrumented sources contribute zero), and the worst observed
  source. All three coverage gates must reach 99%, at least 99% of instrumented
  sources must individually meet the target, and the parent window must be
  `observed` before the objective can become `met`; a measured macro-gate failure
  is `miss`. This evidence covers the received upstream response through
  normalization; upstream catalog completeness still depends on its baseline or
  reported totals and is not proven for every source. Missing or contradictory
  telemetry renders as “Недостаточно наблюдений”, never as 100%.
- Statistics tabs load on demand and preserve `stats`, `stats_q`,
  `stats_format`, `stats_rank`, and `stats_period` in the URL.
- Card rows link to `stats=card` using the English card name. The card module
  combines ranked trends with Battlegrounds minion and hero matches.
- Card inspector → “История изменений” opens on the newest real change of the
  card: the last version whose data differ from the previous one, compared with
  that previous version. Service fields (`local_*` mirrors of images,
  `source_hash`, polling timestamps, raw payload) are not changes. The
  “Только изменения данных” switch (on by default) hides versions that differ
  only in service fields from the lists and the changes table; off, every
  loaded version and field is shown. When the loaded page holds no data change
  the panel says so and offers “Показать ещё” (or “Показать все версии” once
  everything is loaded). The “После” and “До” pictures are the archive of the
  version's own `image_url`; the local mirror (`local_image_url`) is only a
  fallback, because at the moment of a change it still points to the old file.
  The two pictures load with high priority behind a skeleton of their final
  size.
- Card inspector text follows the game: `<b>` is bold, `<i>` italic, `\n` a
  line break; `[x]`, `$N` and `#N` markers are reduced to the number, `_` is a
  non-breaking space, `{0}` shows as `X`, unknown tags are dropped. `@`
  separates variants of the text (base text, then in-hand hints such as
  “(3 left!)”); only the first non-empty variant is shown. Plural forms
  `|4(ход,хода,ходов)` resolve to the form for the number written before them.
  The text is built from React elements; markup from data is never inserted
  into the DOM.
- Card language (catalogue and inspector): the “Язык карт” switch (RU · EN · PL
  · ES; accessible names “Русский”, “Английский”, “Польский”, “Испанский”)
  changes only card data: name, text, flavor and the render. The choice is
  `?lang=en|pl|es` (Russian is the default and has no parameter), written with
  `history.replaceState` (no navigation, no history entry) and kept by filters,
  pages, views, search, reset and `?card=`. The preference is saved in
  `localStorage['hs_card_lang']` (blocked storage means Russian, no errors): URL
  first, then the saved choice, then Russian; a shared `?lang=` link does not
  overwrite it, a choice in the switch does; an unknown value is dropped. Russian
  is the panel's own data and makes no requests. en/pl/es come from the public
  `GET /api/v1/card-catalog/{card_id}?locale=enUS|plPL|esES` (by
  `/dbf/{dbf}` when the row has no card id, and once more by DBF after a 404 on
  the card id), `credentials: 'omit'`, at most 6 requests at once, one per card,
  an in-tab cache, cancellation when the page or language changes, a 10 s
  timeout, a single retry of a 429 after its `Retry-After` (seconds or an
  HTTP-date, at most 5 s; 1.5 s without one). An answer without a translation
  (`fallback: true`) shows English with the note “Нет перевода на … — показан
  английский”; a card the catalogue does not know shows the panel data with
  “Нет в каталоге карт — показаны данные панели”; a network error, 5xx, timeout
  or an invalid answer shows the panel data and one line “Не удалось загрузить
  перевод для N карт” with “Повторить” (only the failed requests are repeated).
  `localized.render_url`, when valid, leads the images of the normal version.
  Plural forms `|4(…)` follow the language of the text: pl and ru differ
  (21 is the first form in ru, the third in pl); en and es have two forms.
- Every statistics row has “Подробнее”. The keyboard-accessible drawer exposes
  every scalar and nested source field, including all HSGuru decks and all BG
  combat rounds; raw JSON remains available for integration/debugging.
- Battlegrounds heroes use square HearthstoneJSON portrait art. Battlegrounds
  minions use the verified local card renders. Failed images keep a stable
  placeholder and are counted in the result metadata.

## Navigation and page states

- The sidebar has four groups: **Каталог** (fifteen categories) and
  **Статистика** (five groups: «Стандарт и Вольный», «Поля сражений», «Арена»,
  «Карты», «Патчи»), then **Мониторинг** (Источники, Реестр источников,
  Провайдеры) and **Управление** (API-токены, Переводы Wiki). Catalogue and
  statistics unfold on their own pages (or by hand), so monitoring stays above
  the fold elsewhere. The breadcrumb names the same group and item as the
  sidebar; in the card editor it links back to the catalogue address the
  editor was opened from.
- Statistics modules are placed by `panel-next/lib/stats-sections.ts`: group,
  order and short tab names. A group page shows Valyro `Tabs` with only the
  modules of that group (groups with one module have no tabs). The registry
  and providers are the monitoring pages, without tabs. A registry module the
  map does not know lands in the «Прочее» group (menu item and tabs appear on
  their own), so it is never lost; `tests/stats-sections.test.ts` covers this.
  `/?action=analytics` without `stats` opens «Мета»; old
  `/?action=analytics&stats=<key>` links keep working. The page heading stays
  the registry title of the module.
- On the source registry (`stats=overview`) long ids wrap inside their column,
  «Что загружается» keeps two lines (full text in «Подробнее»), and the seven
  columns fit 1280 and 1440 px without sideways scrolling.
- The parser page heading matches its menu item, «Источники». A planned run
  whose time has passed renders through `formatSchedule` / `ScheduleTime` as
  «просрочен на …» in the warning colour (the summary card turns warning too),
  never as «N дней назад». A delay under five minutes reads «сейчас».
- Catalogue empty results distinguish «nothing matches the query/filters»
  (with the submitted query and a reset action) from an empty category.
- The card editor groups fields into «Карта» and «Характеристики и пул», marks
  the two required fields, names the edited card under the heading and keeps
  its action row sticky: save and cancel on the left, delete on the right as an
  outlined danger button. Field names, the confirmation dialog and mutations
  are unchanged.
- «Переводы Wiki» shows the term count and missing translations, uses the same
  search field as other pages, shows an empty state when the search matches
  nothing and keeps the save row sticky. Search only hides rows; every term is
  still submitted. Save errors render as an error notice.
- The route error page shows the server `digest` («Код для логов») when Next
  provides one and offers a link back to the catalogue next to «Повторить».
- In data tables the action buttons and «Подробнее»/«Детали» links sit on the
  first text line of a top-aligned row.
- `formatSchedule` has literal boundary tests and fast-check properties
  (a future plan is never overdue, under five minutes late is «сейчас», five
  minutes or more is always «просрочен на …»). The page behaviour above is
  covered by `panel-next/tests/ux-browser.mjs` on fixtures.

## Adding a statistics module

1. Add an allowlisted path and typed query parameters to
   `analytics_module_registry()` in `lib/analytics.php`.
2. Add a normalization branch that returns `summary`, `columns`, and `rows`.
   Keep source-specific response shapes out of the browser renderer.
3. `panel-next` lists modules from `action=analytics_registry`; a module
   missing from `lib/stats-sections.ts` appears under «Прочее». Add it to a
   group there to give it a place and a short tab name. Add special filters
   in `panel-next/components/Analytics.tsx`; prefer the existing generic table.
4. Use a bounded `limit`, a short upstream timeout, and a cache TTL appropriate
   for the source. Never add a user-controlled upstream URL.

## Maintenance rules

1. In `panel-next`, text on filled controls uses `--on-accent` or `--on-bad`,
   never a literal white: the three dark themes have light accents.
   `tests/theme-contrast.test.ts` enforces 4.5:1 for every theme.
2. The Next deck-tile grid uses `auto-fill` columns of at least 256 px. Do not
   restore the shared example's fixed `min-width`; it forces sideways scrolling
   below 1500 px.
3. `panel-next/app/globals.css` defines the scales once: `--space-*`,
   `--radius-*`, `--text-*`, `--z-*`, and per-theme `--good`, `--bad`,
   `--warn`, `--info` with their `-soft` fills. Use them instead of raw px or
   colours; the contrast test covers every text/fill pair.
4. Format values only through `panel-next/lib/format.ts` (dates, relative
   times, numbers, percents, statuses, slugs, field and filter labels) and
   render them through `components/ui.tsx` (`Value`, `StatusBadge`, `Time`,
   `KpiCard`, `EmptyState`). Do not print raw ISO dates, status codes or
   slugs; the parser run history JSON is the one remaining exception.
5. Icons come from `@phosphor-icons/react` with `aria-hidden`; the control
    keeps a text label or an `aria-label`.
6. `npm run check` starts with `prettier --check` and `biome lint`. Biome is
    used instead of ESLint because `eslint-config-next` pulls a dependency with
    an unpatched high advisory and typescript-eslint cannot load TypeScript 7.
    A deliberate exception carries a `biome-ignore` comment with its reason.
7. The open catalogue record lives in `?card=<id>` (`lib/card-detail.ts`:
    `cardKey`, `matchesCard`, `cardHref`). `Catalog` writes it with
    `history.pushState`, which Next syncs into `useSearchParams` without a
    server round trip; arrows use `replaceState`, and closing goes back when
    the card was opened in this tab. Keep one inspector dialog mounted while
    flipping, or the dialog closes, reopens and drops focus. `app/page.tsx`
    strips `card` before calling PHP and resolves a card outside the current
    page with `q=<id>&per_page=150` plus an exact identifier match, because
    PHP search is a LIKE match.
8. Table columns come from `lib/catalog-columns.ts`, one list per category,
    with field names from `panel/sql/schema.mysql.sql`. A new category adds
    its columns and a case in `tests/catalog-columns.test.ts`. Do not add a
    client-side column sort: `next-data.php` has no sort parameter, and
    sorting one page of a paginated list misleads.
9. Ctrl+K is `components/CommandPalette.tsx`, a WAI-ARIA combobox with
    `aria-activedescendant`. Card search goes through
    `GET /api/panel?view=panel&action=list&q=…&per_page=8` for the current
    category, debounced, and aborts stale requests.
10. `.table-scroll` is `position: relative`, so `.sr-only` text inside cells
    scrolls with the table instead of widening the page on phones.

## Verification

Before deployment, from the repository root:

```bash
make panel-check
make platform-check
```

Then open the Next.js panel and verify the default catalogue, one filtered
result, statistics, sources and API tokens at desktop, tablet and mobile widths
with a clean browser console. Test an empty card-statistics search and a known
card such as `Fire Fly`; a golden ID such as `BG31_835_G` must still resolve to
its base-card row.
