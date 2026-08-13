#!/usr/bin/env bash
set -euo pipefail

panel_root="${PANEL_ROOT:-/srv/api-kolodahearthstone/panel/current}"

assert_contains() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  if ! grep -qE "$pattern" "${panel_root}/${file}"; then
    echo "FAIL: ${message}" >&2
    exit 1
  fi
}

assert_not_contains() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  if grep -qE "$pattern" "${panel_root}/${file}"; then
    echo "FAIL: ${message}" >&2
    exit 1
  fi
}

assert_contains index.php 'class="art-preview[^\"]*"' 'art must render as a lightbox thumbnail'
assert_contains index.php 'data-preview="<\?= h\(\$artImage\)' 'art thumbnail must target the full image'
assert_contains index.php 'lastFullscreenTrigger' 'lightbox must restore focus to its trigger'
assert_contains index.php "event\.key === 'Tab'" 'lightbox must trap keyboard focus'
assert_contains partials/analytics-dashboard.php "'hsguru_archetypes'" 'HSGuru archetypes module must be visible'
assert_contains partials/analytics-dashboard.php "'arena_cards'" 'Arena card statistics module must be visible'
assert_contains partials/analytics-dashboard.php 'data-analytics-rating-control' 'hero rating filter must be visible'
assert_contains lib/analytics.php "'constructed_cards'" 'constructed card statistics must be registered'
assert_not_contains index.php '<h2>Инструменты</h2>' 'obsolete tools section must be removed from navigation'
assert_not_contains index.php 'href="/\?action=wiki_terms"' 'Wiki translation tool must not be linked'
assert_not_contains index.php 'href="/\?action=new#add-card"' 'new-card tool must not be linked'
assert_not_contains partials/analytics-dashboard.php 'analytics-catalog-summary' 'redundant catalogue widgets must not obscure the data table'
assert_contains lib/analytics.php 'analytics_source_age_label' 'source overview must expose freshness age'
assert_contains lib/analytics.php "analytics_column\('fetched_at', 'Обновлено', 'date'\)" 'source overview must show update timestamps'
assert_contains lib/analytics.php "analytics_column\('description', 'Что загружается'\)" 'source overview must explain each source'
assert_contains lib/analytics.php 'v1/256x/' 'hero statistics must use portrait artwork instead of card renders'
assert_contains lib/analytics.php 'function analytics_bg_minions' 'BG minions must use the available detailed dataset'
assert_contains lib/analytics.php "'limit' => \['type' => 'int', 'default' => 500, 'min' => 1, 'max' => 500\]" 'BG minions must expose the complete current dataset by default'
assert_contains assets/analytics.js 'analytics-image-placeholder' 'broken images must render a deliberate placeholder'
assert_contains assets/analytics.js 'openDetail' 'analytics rows must open full entity details'
assert_contains assets/analytics.js 'const rows = value;' 'nested detail tables must expose the complete source array'
assert_not_contains assets/analytics.js "new Set\(\)\)\.slice\(0, 10\)" 'nested detail tables must not hide source columns'
assert_contains partials/analytics-dashboard.php 'data-analytics-detail-drawer' 'detail drawer markup must be present'
assert_contains index.php 'href="/\?action=api_tokens"' 'API token manager must be reachable from panel navigation'
assert_contains index.php 'href="/\?action=parsers"' 'parser operations workspace must be reachable from panel navigation'
assert_contains partials/parser-control.php 'data-parser-control' 'parser workspace root must be present'
assert_contains partials/parser-control.php 'data-parser-sources-body' 'parser workspace must expose the source table'
assert_contains partials/parser-control.php 'data-run-dialog' 'manual parser runs must require a confirmation dialog'
assert_contains parser-control.php 'panel_parser_control_require_csrf' 'parser mutations must require CSRF validation'
assert_contains lib/parser_control.php 'Authorization: Bearer' 'parser admin credential must remain in the server bridge'
assert_contains lib/parser_control.php 'PANEL_PARSER_CONTROL_RUN_LIMIT' 'manual parser runs must have an application-level rate limit'
assert_contains assets/parser-control.js 'textContent' 'parser responses must be rendered as text'
assert_contains index.php 'data-table-density' 'large catalogue tables must provide a density control'
assert_contains partials/api-token-manager.php 'data-token-secret' 'new token secret must have a dedicated one-time display'
assert_contains partials/api-token-manager.php 'data-copy-token' 'new token secret must be copyable without persistent browser storage'
assert_not_contains partials/api-token-manager.php 'localStorage' 'API token secrets must never be written to localStorage'
assert_contains lib/api_tokens.php 'http://127\.0\.0\.1:18081' 'panel token operations must use the fixed local API endpoint'
assert_contains lib/api_tokens.php 'PANEL_API_TOKEN_ISSUE_LIMIT' 'token issuance must have an application-level rate limit'
assert_contains lib/api_tokens.php 'hash_equals' 'manager self-revoke protection must use a timing-safe comparison'

echo 'OK: admin UI contract'
