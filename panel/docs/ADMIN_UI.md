# HS Data admin UI

## Purpose

`api.kolodahearthstone.com` is an operational catalogue for Hearthstone data. Its UI is
optimized for fast scanning and maintenance rather than for public browsing.
The server-rendered page remains intentionally dependency-free: PHP renders the
catalogues, CSS provides the responsive design system, and a small inline script
handles themes, automatic filters, previews, the mobile menu, and shortcuts.

The trinket catalogue has a dedicated **Full art** column. It shows the local
square original without a card frame and opens it through the shared lightbox.
The import and API contracts are documented in `LIBRARY_FULL_ART.md`.

## Source map

- `index.php` — queries, server-rendered markup, filter state, and lightweight UI
  behavior.
- `assets/style.css` — theme tokens, reusable components, catalogue layouts, and
  responsive behavior.
- `partials/analytics-dashboard.php` — statistics navigation, compact filters,
  accessible loading/error containers, and the shared entity-detail drawer.
- `analytics.php` + `lib/analytics.php` — protected, read-only allowlist gateway
  to the local Hearthstone Data API. New statistics modules are registered in
  `analytics_module_registry()` and normalized before reaching the browser.
- `assets/analytics.js` — URL-backed statistics state and generic table
  rendering. It never receives internal API credentials or arbitrary URLs.
- `assets/parsing-reliability.js` — dependency-free view model for observed and
  collecting parser-reliability states; the same pure logic is exercised in
  Node tests.
- `partials/parser-control.php` — parser operations workspace, source table,
  recent runs, and the explicit manual-run confirmation dialog.
- `parser-control.php` + `lib/parser_control.php` — narrow authenticated bridge
  for the local parser control API. The browser never receives its admin token.
- `assets/parser-control-view.js` + `assets/parser-control.js` — tested parser
  view model and DOM controller with adaptive automatic refresh.
- `api/index.php` — public API; it is not coupled to the admin presentation.

## Interaction contracts

- Filters use URL query parameters and stay shareable/bookmarkable.
- Changing a select submits immediately; search is debounced by 520 ms.
- Pressing `/` focuses the primary catalogue search when focus is not already in
  a form control.
- On screens up to 1120 px the navigation is collapsed behind an accessible menu
  button.
- Card, golden, and framed previews use the shared `data-preview` lightbox.
- Golden variants are represented inside their base-card row, but their IDs and
  DBFs are also searchable.
- The action column remains sticky during horizontal table scrolling.
- Catalogue and statistics tables provide a persistent compact-density option.
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
  out outcomes remain visible as counts. A percentage is shown only for an
  `observed` window with eligible attempts and internally consistent counts.
  This scope covers generic refresh sources; dedicated pipelines are explicitly
  excluded for now, and best-effort telemetry cannot detect every write gap.
  Missing, malformed, `collecting`, or legacy estimated telemetry renders as
  “Накапливаем статистику” and never falls back to a synthetic 100%.
- Statistics tabs load on demand and preserve `stats`, `stats_q`,
  `stats_format`, `stats_rank`, and `stats_period` in the URL.
- Card rows link to `stats=card` using the English card name. The card module
  combines ranked trends with Battlegrounds minion and hero matches.
- Every statistics row has “Подробнее”. The keyboard-accessible drawer exposes
  every scalar and nested source field, including all HSGuru decks and all BG
  combat rounds; raw JSON remains available for integration/debugging.
- Battlegrounds heroes use square HearthstoneJSON portrait art. Battlegrounds
  minions use the verified local card renders. Failed images keep a stable
  placeholder and are counted in the result metadata.
- “Новая карта” and “Переводы Wiki” are intentionally absent from navigation;
  catalogue and analytics workflows are the primary interface.

## Adding a statistics module

1. Add an allowlisted path and typed query parameters to
   `analytics_module_registry()` in `lib/analytics.php`.
2. Add a normalization branch that returns `summary`, `columns`, and `rows`.
   Keep source-specific response shapes out of the browser renderer.
3. Add the navigation button to `partials/analytics-dashboard.php`.
4. If the module needs special filters, expose them with semantic labels and
   wire them in `assets/analytics.js`. Prefer the existing generic table.
5. Use a bounded `limit`, a short upstream timeout, and a cache TTL appropriate
   for the source. Never add a user-controlled upstream URL.

## Responsive rules

- Above 1440 px: persistent sidebar and full data table.
- 1121–1440 px: navigation becomes a horizontal catalogue header and secondary
  BG columns are hidden to keep primary data readable.
- Up to 1120 px: compact menu, full-width workspace, and wrapped controls.
- Up to 680 px: two-column filters, compact pagination, and horizontal table
  scrolling for data that cannot be represented safely as cards.

## Maintenance rules

1. Reuse the semantic color and spacing tokens in `:root`; do not add isolated
   hard-coded palettes for one catalogue.
2. New filter controls require an accessible label and must preserve query state.
3. New image previews should use `data-preview`, `data-tooltip`, keyboard focus,
   and an explicit accessible name.
4. Preserve `loading="lazy"` and `decoding="async"` for table media.
5. Increment the `style.css` query version in `index.php` after visible CSS
   changes so browsers and proxies receive the new interface.

## Verification

Before deployment:

```bash
/opt/php74/bin/php -l index.php
/opt/php74/bin/php -l analytics.php
/opt/php74/bin/php -l lib/analytics.php
node --check assets/analytics.js
node --check assets/parsing-reliability.js
node --check assets/parser-control.js
node --test tests/parser_control_view.test.js
node --test tests/parsing_reliability_view.test.js
php tests/parsing_reliability_test.php
php tests/parser_control_test.php
PANEL_ROOT="$PWD" /srv/projects/data/hs-data-platform/tests/admin-ui-contract.sh
```

Then render the default catalogue and at least one filtered result at desktop,
tablet, and mobile widths. Verify that the browser console is clean, the menu,
statistics tabs, scrollable table, detail drawer, and lightbox are
keyboard-accessible. Verify the source registry, a HSGuru archetype with decks,
a BG minion with combat rounds, and BG hero portrait dimensions. Test an empty
card-statistics search and a known card such as `Fire Fly`; a golden ID such as
`BG31_835_G` must still resolve to its base-card row.
