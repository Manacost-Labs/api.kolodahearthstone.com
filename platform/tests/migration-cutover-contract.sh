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

# The runner applies every platform/sql/NNN_*.sql in order, so a migration is
# deployed once its file exists there under the canonical name.
assert_migration() {
  local version="$1"
  local message="$2"
  if [[ ! -f "${project_root}/sql/${version}.sql" ]]; then
    echo "FAIL: ${message}" >&2
    exit 1
  fi
}

assert_contains scripts/apply-migrations.sh \
  "/sql\" -maxdepth 1 -name '\\[0-9\\]\\[0-9\\]\\[0-9\\]_\\*\\.sql'" \
  'the migration runner must discover every numbered migration'

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
assert_contains scripts/sync-shadow.php \
  "authoritative_replace_tables text\[\] := ARRAY" \
  'tables with unstable surrogate ids need an explicit atomic replacement policy'
assert_contains scripts/sync-shadow.php \
  "'catalog\.battlegrounds_card_wiki_related'" \
  'wiki relationship rows must not conflict when MariaDB recreates their ids'
assert_contains scripts/sync-shadow.php \
  '= ANY\(authoritative_replace_tables\)' \
  'authoritative replacement must run before generic primary-key upsert'
assert_contains scripts/verify-platform.sh \
  "public_status.* == '302'.*public_status.* == '307'" \
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
  "api_token_usage_monthly" \
  'private per-token usage must stay out of the public PostgreSQL shadow'
assert_contains scripts/verify-data.php \
  '!in_array\(\$table, PRIVATE_ANALYTICS_TABLES, true\)' \
  'private API tables must be excluded from analytics parity and key checks'
assert_contains scripts/index-media-assets.php \
  '/srv/api-kolodahearthstone/panel-data/uploads' \
  'media indexing must use canonical persistent storage'
assert_not_contains scripts/verify-platform.sh \
  '/var/www/koloda/data/www/db\.kolodahs\.ru' \
  'health checks must not depend on the retired domain runtime'
assert_migration 006_card_catalog_pagination \
  'the canonical GraphQL cards cursor indexes must be deployed'
assert_contains sql/006_card_catalog_pagination.sql \
  'battlegrounds_cards_catalog_cursor_idx' \
  'Battlegrounds cards must have a keyset pagination index'
assert_contains sql/006_card_catalog_pagination.sql \
  'constructed_cards_catalog_cursor_idx' \
  'constructed cards must have a keyset pagination index'
assert_migration 007_large_collection_pagination \
  'all large GraphQL collections must deploy cursor indexes'
assert_contains sql/007_large_collection_pagination.sql \
  'game_stat_snapshots_cursor_idx' \
  'statistics history must have a keyset pagination index'
assert_contains sql/007_large_collection_pagination.sql \
  'bg_minion_snapshots_cursor_idx' \
  'Battlegrounds minion history must have a keyset pagination index'
assert_migration 008_unified_search_and_history \
  'unified search and patch history indexes must be deployed'
assert_contains sql/008_unified_search_and_history.sql \
  'CREATE OR REPLACE VIEW hub\.unified_search' \
  'the cross-entity search view must be installed'
assert_contains sql/008_unified_search_and_history.sql \
  'game_stat_rows_entity_history_idx' \
  'per-entity patch history must have a lookup index'
assert_migration 009_horizontal_art \
  'horizontal artwork must be exposed by the canonical GraphQL catalogue'
assert_contains sql/009_horizontal_art.sql \
  'horizontal_image_url' \
  'the horizontal artwork migration must publish a dedicated URL'
assert_migration 010_battlegrounds_card_overrides \
  'the shadow catalogue must know the manual card corrections table'
assert_contains sql/010_battlegrounds_card_overrides.sql \
  'PRIMARY KEY \(card_id, field_name\)' \
  'manual corrections must upsert by card and field'

echo 'OK: migration cutover contract'

assert_contains sql/011_card_versions.sql 'catalog.catalog_card_versions' 'card history needs a mirror table before source activation'
assert_contains sql/011_card_versions.sql 'snapshot_json jsonb NOT NULL' 'card snapshot JSON must match the shadow loader JSONB conversion'
assert_migration 011_card_versions 'the migration runner must install card history'
assert_contains scripts/sync-shadow.php "'catalog.catalog_card_versions'" 'card history must use append-only shadow merge'

assert_contains sql/012_bartender_catalog.sql 'catalog.battlegrounds_bartender_skins' 'bartender catalog must be mirrored before activation'
assert_contains sql/012_bartender_catalog.sql 'source_payload_json jsonb' 'bartender JSON must match the shadow loader JSONB conversion'
assert_migration 012_bartender_catalog 'the migration runner must install bartender catalog'

assert_contains sql/015_full_card_catalog.sql 'catalog.hearthstone_card_catalog' 'the full card catalogue must be mirrored before shadow merge'
assert_contains sql/015_full_card_catalog.sql 'dbf bigint NOT NULL UNIQUE' 'catalogue dbf must match the loader type and stay unique'
assert_migration 015_full_card_catalog 'the migration runner must install the full card catalogue'
assert_contains sql/016_bg_minion_stats.sql 'analytics.bg_minion_stat_positions' 'minion statistics detail tables must exist before the analytics delta stages them'
assert_migration 016_bg_minion_stats 'the migration runner must install the minion statistics tables'
assert_contains scripts/stage-analytics.py "'bg_minion_stat_snapshots'" 'minion statistics snapshots must be mirrored by the analytics delta'
assert_contains sql/017_hsguru_mulligan.sql 'analytics.hsguru_mulligan_cards' 'HSGuru mulligan tables must exist before the analytics delta stages them'
assert_migration 017_hsguru_mulligan 'the migration runner must install the HSGuru mulligan tables'
assert_contains scripts/stage-analytics.py "'hsguru_stat_targets'" 'HSGuru mulligan targets must be mirrored by the analytics delta'
assert_contains sql/019_hsreplay_mulligan.sql 'analytics.hsreplay_mulligan_cards' 'HSReplay mulligan tables must exist before the analytics delta stages them'
assert_migration 019_hsreplay_mulligan 'the migration runner must install the HSReplay mulligan tables'
assert_contains scripts/stage-analytics.py "'hsreplay_mulligan_targets'" 'HSReplay mulligan targets must be mirrored by the analytics delta'
assert_contains sql/020_hsreplay_card_choices.sql 'analytics.hsreplay_card_choice_rows' 'HSReplay card choice tables must exist before the analytics delta stages them'
assert_migration 020_hsreplay_card_choices 'the migration runner must install the HSReplay card choice tables'
assert_contains scripts/stage-analytics.py "'hsreplay_card_choice_targets'" 'HSReplay card choice targets must be mirrored by the analytics delta'
assert_contains sql/018_hsguru_streamers.sql 'analytics.hsguru_streamer_decks' 'HSGuru streamer tables must exist before the analytics delta stages them'
assert_migration 018_hsguru_streamers 'the migration runner must install the HSGuru streamer tables'
assert_contains scripts/stage-analytics.py "'hsguru_streamers'" 'HSGuru streamers must be mirrored by the analytics delta'

assert_contains systemd/kolodahs-card-history-media.service 'flock -E 75 -w 900' 'a busy sync lock must end the card history turn with a distinct code'
assert_contains systemd/kolodahs-card-history-media.service 'SuccessExitStatus=75' 'a skipped card history turn must not mark the unit failed'
