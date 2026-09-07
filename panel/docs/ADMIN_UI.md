# HS Data admin UI

## Purpose

`api.kolodahearthstone.com` is an operational catalogue for Hearthstone data. Its UI is
optimized for fast scanning and maintenance rather than for public browsing.
The server-rendered page remains intentionally dependency-free: PHP renders the
catalogues, CSS provides the responsive design system, and dependency-free
JavaScript handles themes, automatic filters, previews, the mobile menu, and
shortcuts.

The trinket catalogue has a dedicated **Full art** column. It shows the local
square original without a card frame and opens it through the shared lightbox.
The import and API contracts are documented in `LIBRARY_FULL_ART.md`.

## Source map

- `index.php` — authenticated dispatch, queries, POST handlers, filter state, and
  composition of the shared presentation partials.
- `assets/style.css` — theme tokens, reusable components, catalogue layouts, and
  responsive behavior.
- `assets/workspace.css` — shared white/blue workspace for all authenticated pages;
  loaded after legacy module styles. Uses semantic tokens so saved dark, tavern,
  and arcane themes still work. New visitors start in light mode.
- `partials/sidebar.php`, `partials/topbar.php`, `lib/panel_shell.php` — common
  navigation, authenticated account controls, and static SVG icons. Catalogues
  remain reachable through an expandable group; the sources route stays
  `/?action=parsers`. Logout remains a CSRF-protected POST.
- `assets/workspace.js` — theme preference and mobile menu toggle, shared with
  isolated browser fixtures.
- `partials/catalog-heading.php`, `partials/catalog-controls.php`, and
  `partials/catalog-pagination.php` — shared catalogue presentation; SQL, row
  actions, and CSRF handling stay in their existing server-side paths.
- `partials/catalog-content.php` + `lib/catalog_view.php` — the actual catalogue
  tables/galleries and pure presentation helpers, separated from database access
  so all entity variants can be rendered in isolated browser tests.
- `partials/media-preview.php` + `assets/media-preview.js` — shared image/video
  lightbox, hover previews, keyboard focus containment and restoration. The
  underlying workspace becomes inert while the lightbox is open.
- `assets/table-controls.js` — catalogue auto-submit and persistent density,
  also loaded by the isolated catalogue/statistics/token fixtures.
- `partials/analytics-dashboard.php` — statistics navigation, compact filters,
  accessible loading/error containers, and the shared entity-detail drawer.
- `analytics.php` + `lib/analytics.php` — protected, read-only allowlist gateway
  to the local Hearthstone Data API. New statistics modules are registered in
  `analytics_module_registry()` and normalized before reaching the browser.
- `assets/analytics.js` — URL-backed statistics state and generic table
  rendering. It never receives internal API credentials or arbitrary URLs.
- `assets/panel-ui.js` — shared command palette, mobile navigation behavior and
  persistent per-table column visibility. Labels are always derived through
  `textContent`; preferences contain column indexes only.
- `partials/command-palette.php` — allowlisted quick navigation available from
  every authenticated page through `Ctrl/⌘ K`.
- `assets/parsing-reliability.js` — dependency-free view model for observed and
  collecting parser-reliability states; the same pure logic is exercised in
  Node tests.
- `partials/parser-control.php` — parser operations workspace, source table,
  recent runs, and the explicit manual-run confirmation dialog.
- `partials/api-token-manager.php` + `assets/token-controls.js` — token registry,
  optional issuance form and one-time secret copying. Secrets are never persisted
  in browser storage; server issuance/revocation and CSRF checks are unchanged.
- `partials/card-editor.php` — new/edit forms with labelled fieldsets, upload
  guidance and allowlisted field retention after a rejected save. Existing POST
  names, checkbox semantics, SQL and file validation are unchanged.
- `partials/wiki-terms.php` + `assets/editor-controls.js` — translation groups,
  search/status filters, result counts and a resettable empty state. Editing a
  row does not change the selected filter; hidden rows remain in the form.
- `partials/auth-page.php` + `assets/auth.css` — shared login/setup/access-denied
  and other auth messages. `lib/auth.php` delegates only presentation to this
  template; GitHub OAuth, sessions, status codes and security headers are intact.
- `parser-control.php` + `lib/parser_control.php` — narrow authenticated bridge
  for the local parser control API. The browser never receives its admin token.
- `assets/parser-control-view.js` + `assets/parser-control.js` — tested parser
  view model and DOM controller with adaptive automatic refresh.
- `api/index.php` — public API; it is not coupled to the admin presentation.

## Interaction contracts

- Filters use URL query parameters and stay shareable/bookmarkable.
- Changing a catalogue select submits immediately; search is debounced by
  520 ms (two characters minimum, or an empty query). Manual submit/select
  cancels a pending search timer, so a change cannot submit twice.
- Catalogue search and section selection are always visible. Native
  “Дополнительные фильтры” disclose the section-specific options; active
  advanced filters start expanded. Pagination preserves the active query.
- Technical columns start hidden for new preferences across Battlegrounds,
  Constructed, hero, timewarped and library tables, but remain available through
  the column picker. Existing saved preferences take priority.
- Skins, pets and coins use responsive galleries with normal page scrolling;
  table-only column/density/scroll controls are not rendered for those sections.
- The BG navigation item remains active for both minion and spell filters.
- Pressing `/` focuses the primary catalogue search when focus is not already in
  a form control.
- On screens up to 760 px the navigation is collapsed behind an accessible menu
  button.
- Card, golden, and framed previews use the shared `data-preview` lightbox.
- Lightbox Tab/Shift+Tab containment includes fixed-position controls. Escape
  closes it and restores the trigger; background search/navigation shortcuts
  cannot move focus out of the open preview.
- Golden variants are represented inside their base-card row, but their IDs and
  DBFs are also searchable.
- Catalogue action columns remain sticky on desktop. On mobile the entire
  table scrolls, so identity and action cells cannot cover the middle columns.
- Catalogue and statistics tables provide a persistent compact-density option.
- Large catalogue, analytics, parser, and token tables expose a shared column
  picker. The first identity column and final action column stay visible; each
  module stores only its hidden column indexes in local browser storage.
- Every horizontally overflowing table exposes the same explicit left/right
  navigation with start, percentage, and end feedback. It remains hidden when
  all columns fit, so compact tables do not gain redundant controls.
- The Sources workspace prioritizes unavailable/fallback sources. Its five
  initial columns show identity, published data, last attempt, next run and the
  manual action. Schedules and row counts are available through “Колонки”; saved
  choices survive data refresh. Error diagnostics remain expandable. On small
  screens the table scrolls inside its panel without widening the entire page.
- Source search, status and section use `source_q`, `source_status` and
  `source_section` URL parameters. A clear action resets an empty filtered view.
- State GET requests have a 15-second deadline; failed refresh preserves the
  previous snapshot with a stale-state warning. Polling runs every 12 seconds
  during a run and every minute otherwise; it pauses while the tab is hidden,
  a run dialog is open, or source/run details are being read.
- Manual POSTs are single-flight with a 20-second deadline. An uncertain network
  or server failure never triggers an automatic retry: the user is asked to
  check history first. A confirmed rejection can be retried explicitly.
- Manual parser runs require GitHub authentication, a same-origin CSRF token,
  an application-level rate budget and an explicit confirmation dialog.
- Statistics modules use one labelled selector; only applicable filters are
  shown. Refresh and table preferences remain available outside those filters.
  Module, query, format, rank, period, and BG Solo/Duos selection survive reload.
- Statistics GET requests have a 15-second deadline and reject stale responses
  after switching modules, including transports that ignore cancellation.
  Invalid table payloads are not cached. Failed requests show an explicit retry;
  they do not label old data as a newly loaded result.
- The “Обзор и статистика” workspace opens with the complete source registry. It shows
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
- Statistics sections load on demand and preserve `stats`, `stats_q`,
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
- Token issuance is collapsed until requested and stays open after a validation
  error. The configured-key badge describes configuration, not unverified backend
  connectivity. Revocation still requires explicit confirmation; manager-token
  self-revocation protection remains server-side.

## Completed presentation coverage

| Workspace | Covered views |
| --- | --- |
| Overview and statistics | All 12 registered selectors, applicable filters and entity details |
| Sources | Summary, source registry, run history, diagnostics and run confirmation |
| Catalogue tables | All BG, minions, spells, Constructed, heroes, timewarped, anomalies, quests, prizes, rewards, trinkets |
| Catalogue galleries | Hero skins, pets, coins and nested media |
| API access | Registry, issuance, one-time secret, revoke, configuration/error/empty states |
| Editing tools | New card, edit card, Wiki translations |
| Authentication | Shared login/setup, denied/expired, upstream-error and logout messages |

This describes source implementation, not a production deployment. Public API
and JSON bridge endpoints are not presentation pages and were not redesigned.

## Adding a statistics module

1. Add an allowlisted path and typed query parameters to
   `analytics_module_registry()` in `lib/analytics.php`.
2. Add a normalization branch that returns `summary`, `columns`, and `rows`.
   Keep source-specific response shapes out of the browser renderer.
3. Add the module label to `$statisticsModules` in
   `partials/analytics-dashboard.php`; it becomes an option in the selector.
4. If the module needs special filters, expose them with semantic labels and
   wire them in `assets/analytics.js`. Prefer the existing generic table.
5. Use a bounded `limit`, a short upstream timeout, and a cache TTL appropriate
   for the source. Never add a user-controlled upstream URL.

## Responsive rules

- Above 1180 px: persistent 248 px sidebar and full-width workspace content.
- 761–1180 px: 220 px sidebar; Sources summary uses two columns.
- Up to 760 px: accessible collapsible menu, full-width workspace and stacked
  Source filters. Theme controls are available inside the expanded menu.
- Wide data tables scroll inside their panels with explicit left/right controls;
  the entire page must not overflow at 320, 390, 768, 1024 or 1440 px.

## Maintenance rules

1. Reuse the semantic color and spacing tokens in `:root`; do not add isolated
   hard-coded palettes for one catalogue.
2. New filter controls require an accessible label and must preserve query state.
3. New image previews should use `data-preview`, `data-tooltip`, keyboard focus,
   and an explicit accessible name.
4. Preserve `loading="lazy"` and `decoding="async"` for table media.
5. Increment the affected asset query version in `index.php` after visible CSS
   or JavaScript changes so browsers and proxies receive the new interface.

## Verification

From the repository root, `make check` is the canonical API/panel/platform/SDK
gate. `make security` runs the repository security checks. If the checkout has
no `.venv`, supply a tested interpreter with `make check PYTHON=/path/to/python`
or provision the development environment first.

`make panel-browser-check` runs additional isolated browser regressions using
Python Playwright and Chromium. Override `BROWSER_PYTHON` / `PANEL_CHROMIUM` when
needed. The suite starts its own loopback-only PHP server, uses shared production
partials with fixture data, blocks external requests, and mocks every parser
operation. It covers 320–1440 px layouts, all shared-shell modules, themes,
keyboard navigation, URL filters, persistent columns, empty/error/loading states,
request deadlines, text escaping and single-flight manual-run confirmation.
There are 34 browser scenarios, with parameterized cases for all 14 catalogue
filter variants, empty/missing-media states and keyboard preview controls.
Set `PANEL_SCREENSHOT_DIR` to an existing directory to capture desktop/mobile
Sources, catalogue variants, statistics, tokens, editor, Wiki and auth screenshots.
The pagination fixture uses synthetic BG rows; `catalog_variants_fixture.php`
renders every actual production catalogue branch and view helper with synthetic
records and local test media. PHP warnings fail the variant fixture explicitly.
Fixtures do not prove production SQL/data, successful real uploads, OAuth,
live token issuance/revocation or producer outcomes; those remain authorized
release verification steps. Tests are excluded
from panel release artifacts by the existing runtime-layout contract.

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
statistics selector, scrollable table, detail drawer, and lightbox are
keyboard-accessible. Verify the source registry, a HSGuru archetype with decks,
a BG minion with combat rounds, and BG hero portrait dimensions. Test an empty
card-statistics search and a known card such as `Fire Fly`; a golden ID such as
`BG31_835_G` must still resolve to its base-card row.
