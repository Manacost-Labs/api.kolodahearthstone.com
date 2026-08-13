#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

assert_contains() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  if ! grep -qE "$pattern" "${project_root}/${file}"; then
    echo "FAIL: ${message}" >&2
    exit 1
  fi
}

assert_not_contains() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  if grep -qE "$pattern" "${project_root}/${file}"; then
    echo "FAIL: ${message}" >&2
    exit 1
  fi
}

assert_contains scripts/bootstrap-shadow.php \
  "INCLUDING ONLY TABLE NAMES LIKE 'archetype_deck_cards'" \
  'SQLite analytics migration must use a public-table allowlist'
assert_contains scripts/bootstrap-shadow.php \
  "'fetch_log'" \
  'the public-table allowlist must preserve parser fetch telemetry'
assert_not_contains scripts/bootstrap-shadow.php \
  "INCLUDING ONLY TABLE NAMES LIKE '%'" \
  'a wildcard inclusion would import future private API tables'
assert_contains scripts/bootstrap-shadow.php \
  "EXCLUDING TABLE NAMES LIKE 'api_tokens'" \
  'API token digests must never enter the PostgreSQL shadow import'
assert_contains scripts/bootstrap-shadow.php \
  "const CATALOG_CONFIG = '/etc/api-kolodahearthstone/panel-config.php'" \
  'catalogue imports must use the domain-neutral private panel config'
assert_contains scripts/bootstrap-shadow.php \
  'function createPublicAnalyticsSnapshot' \
  'analytics migration must use a sanitized SQLite snapshot'
assert_contains scripts/bootstrap-shadow.php \
  'DROP TABLE IF EXISTS api_tokens' \
  'the sanitized snapshot must remove private API tokens before migration'
assert_contains scripts/bootstrap-shadow.php \
  'DROP INDEX IF EXISTS idx_bg_minion_round_stats_snapshot' \
  'the sanitized snapshot must remove the redundant composite-key index'
assert_contains scripts/bootstrap-shadow.php \
  "sqlite://.*analyticsSnapshot" \
  'pgloader must read the sanitized snapshot instead of the live API database'
assert_contains scripts/sync-shadow.php \
  'DROP SCHEMA IF EXISTS catalog_stage CASCADE' \
  'each synchronization must remove stale catalogue staging tables'
assert_contains scripts/sync-shadow.php \
  'DROP SCHEMA IF EXISTS analytics_stage CASCADE' \
  'each synchronization must remove stale analytics staging tables'
assert_contains scripts/sync-shadow.php \
  "target\.%1\\\$I = source\.%1\\\$I" \
  'shadow cleanup must use indexable primary-key equality'
assert_not_contains scripts/sync-shadow.php \
  'IS NOT DISTINCT FROM source' \
  'shadow cleanup must not disable primary-key index lookups'
if [[ "$(grep -c '= ANY(append_only_tables)' "${project_root}/scripts/sync-shadow.php")" -lt 2 ]]; then
  echo 'FAIL: append-only history must never run destructive cleanup joins' >&2
  exit 1
fi
assert_contains scripts/verify-platform.sh \
  "test .*public_status.* = '302'" \
  'the protected panel root must be monitored as a GitHub OAuth redirect'
assert_contains scripts/verify-platform.sh \
  'https://api.kolodahearthstone.com/auth/github' \
  'the panel redirect must point to the canonical GitHub sign-in entrypoint'
assert_contains scripts/verify-platform.sh \
  'https://github.com/login/oauth/authorize' \
  'the GitHub sign-in entrypoint must redirect to GitHub OAuth'
assert_not_contains scripts/verify-platform.sh \
  'admin_html=' \
  'the health check must not try to render a protected page without a session'
assert_contains scripts/verify-platform.sh \
  'sudo -n -u koloda php -l' \
  'protected panel files must be linted with the production owner permissions'
assert_contains scripts/verify-platform.sh \
  '/srv/api-kolodahearthstone/panel/current' \
  'health checks must use the canonical panel runtime'
assert_contains scripts/verify-data.php \
  "PRIVATE_ANALYTICS_TABLES" \
  'data parity must explicitly distinguish private API tables'
assert_contains scripts/verify-data.php \
  '!in_array\(\$table, PRIVATE_ANALYTICS_TABLES, true\)' \
  'private API tables must be excluded from analytics parity and key checks'
assert_contains scripts/index-media-assets.php \
  '/srv/api-kolodahearthstone/panel-data/uploads' \
  'media indexing must use canonical persistent storage'
assert_not_contains scripts/verify-platform.sh \
  '/var/www/koloda/data/www/db\.kolodahs\.ru' \
  'health checks must not depend on the retired domain runtime'
assert_contains scripts/apply-migrations.sh \
  '006_card_catalog_pagination\.sql' \
  'the canonical GraphQL cards cursor indexes must be deployed'
assert_contains sql/006_card_catalog_pagination.sql \
  'battlegrounds_cards_catalog_cursor_idx' \
  'Battlegrounds cards must have a keyset pagination index'
assert_contains sql/006_card_catalog_pagination.sql \
  'constructed_cards_catalog_cursor_idx' \
  'constructed cards must have a keyset pagination index'
assert_contains scripts/apply-migrations.sh \
  '007_large_collection_pagination\.sql' \
  'all large GraphQL collections must deploy cursor indexes'
assert_contains sql/007_large_collection_pagination.sql \
  'game_stat_snapshots_cursor_idx' \
  'statistics history must have a keyset pagination index'
assert_contains sql/007_large_collection_pagination.sql \
  'bg_minion_snapshots_cursor_idx' \
  'Battlegrounds minion history must have a keyset pagination index'
assert_contains scripts/apply-migrations.sh \
  '008_unified_search_and_history\.sql' \
  'unified search and patch history indexes must be deployed'
assert_contains sql/008_unified_search_and_history.sql \
  'CREATE OR REPLACE VIEW hub\.unified_search' \
  'the cross-entity search view must be installed'
assert_contains sql/008_unified_search_and_history.sql \
  'game_stat_rows_entity_history_idx' \
  'per-entity patch history must have a lookup index'
assert_contains scripts/apply-migrations.sh \
  '009_horizontal_art\.sql' \
  'horizontal artwork must be exposed by the canonical GraphQL catalogue'
assert_contains sql/009_horizontal_art.sql \
  'horizontal_image_url' \
  'the horizontal artwork migration must publish a dedicated URL'

echo 'OK: migration cutover contract'
