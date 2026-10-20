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

assert_contains lib/analytics.php "'constructed_cards'" 'constructed card statistics must be registered'
assert_contains lib/analytics.php 'analytics_source_age_label' 'source overview must expose freshness age'
assert_contains lib/analytics.php "analytics_column\('fetched_at', 'Обновлено', 'date'\)" 'source overview must show update timestamps'
assert_contains lib/analytics.php "analytics_column\('description', 'Что загружается'\)" 'source overview must explain each source'
assert_contains lib/analytics.php 'v1/256x/' 'hero statistics must use portrait artwork instead of card renders'
assert_contains lib/analytics.php 'function analytics_bg_minions' 'BG minions must use the available detailed dataset'
assert_contains lib/analytics.php "'limit' => \['type' => 'int', 'default' => 500, 'min' => 1, 'max' => 500\]" 'BG minions must expose the complete current dataset by default'
assert_contains parser-control.php 'panel_parser_control_require_csrf' 'parser mutations must require CSRF validation'
assert_contains lib/parser_control.php 'Authorization: Bearer' 'parser admin credential must remain in the server bridge'
assert_contains lib/parser_control.php 'PANEL_PARSER_CONTROL_RUN_LIMIT' 'manual parser runs must have an application-level rate limit'
assert_contains lib/api_tokens.php 'http://127\.0\.0\.1:18081' 'panel token operations must use the fixed local API endpoint'
assert_contains lib/api_tokens.php 'PANEL_API_TOKEN_ISSUE_LIMIT' 'token issuance must have an application-level rate limit'
assert_contains lib/api_tokens.php 'hash_equals' 'manager self-revoke protection must use a timing-safe comparison'

for retired in index.php partials assets/style.css assets/panel-ui.js assets/analytics.js; do
  if [[ -e "${panel_root}/${retired}" ]]; then
    echo "FAIL: the retired PHP view must not return (${retired})" >&2
    exit 1
  fi
done

echo 'OK: admin UI contract'
