#!/usr/bin/env bash
set -euo pipefail

platform_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_dir="${platform_dir}/postgres"

sudo -n docker compose --project-directory "${compose_dir}" -f "${compose_dir}/docker-compose.yml" exec -T postgres \
  sh -eu -c '
    if ! psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
      "SELECT 1 FROM pg_database WHERE datname = '\''hs_data'\''" | grep -qx 1; then
      createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" hs_data
    fi
  '

sudo -n docker compose --project-directory "${compose_dir}" -f "${compose_dir}/docker-compose.yml" exec -T postgres \
  sh -eu -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data' \
  < "${platform_dir}/sql/001_platform.sql"

for migration in \
  "${platform_dir}/sql/002_jsonb_and_hub.sql" \
  "${platform_dir}/sql/003_service_roles.sql" \
  "${platform_dir}/sql/004_game_statistics.sql" \
  "${platform_dir}/sql/005_media_assets.sql" \
  "${platform_dir}/sql/006_card_catalog_pagination.sql" \
  "${platform_dir}/sql/007_large_collection_pagination.sql" \
  "${platform_dir}/sql/008_unified_search_and_history.sql"
do
  sudo -n docker compose --project-directory "${compose_dir}" -f "${compose_dir}/docker-compose.yml" exec -T postgres \
    sh -eu -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data' \
    < "${migration}"
done
