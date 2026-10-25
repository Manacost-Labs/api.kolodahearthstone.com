#!/usr/bin/env bash
# Apply platform/sql migrations in order, each exactly once.
#
# platform.schema_migrations is the ledger: a migration whose version (file
# name without .sql) is already recorded is skipped, so re-running this script
# is safe. Replaying is not: early migrations define hub views that later ones
# widen, and PostgreSQL refuses CREATE OR REPLACE VIEW with fewer columns.
#
# Usage: apply-migrations.sh [--dry-run]
#   --dry-run  list pending migrations without changing the database
#
# Environment (defaults target the production platform compose stack):
#   PLATFORM_DOCKER          docker command prefix (default: "sudo -n docker")
#   PLATFORM_PSQL_CONTAINER  run psql in this container via `docker exec`
#                            instead of the platform compose service
set -euo pipefail

platform_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_dir="${platform_dir}/postgres"
database=hs_data
dry_run=0

case "${1:-}" in
  "") ;;
  --dry-run) dry_run=1 ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

read -r -a docker_cmd <<< "${PLATFORM_DOCKER:-sudo -n docker}"
if [[ -n "${PLATFORM_PSQL_CONTAINER:-}" ]]; then
  exec_prefix=("${docker_cmd[@]}" exec -i "${PLATFORM_PSQL_CONTAINER}")
else
  exec_prefix=("${docker_cmd[@]}" compose --project-directory "${compose_dir}"
    -f "${compose_dir}/docker-compose.yml" exec -T postgres)
fi

# psql_in <database|-> [psql args...]; "-" is the container's POSTGRES_DB.
# stdin is forwarded to psql.
psql_in() {
  # shellcheck disable=SC2016 # expanded by the container shell
  "${exec_prefix[@]}" sh -eu -c \
    'db="$1"; shift; [ "$db" = - ] && db="$POSTGRES_DB"; exec psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" "$@"' \
    sh "$@"
}

migrations=()
while IFS= read -r file; do
  migrations+=("${file}")
done < <(find "${platform_dir}/sql" -maxdepth 1 -name '[0-9][0-9][0-9]_*.sql' | LC_ALL=C sort)
if [[ ${#migrations[@]} -eq 0 ]]; then
  echo "no migrations found in ${platform_dir}/sql" >&2
  exit 1
fi

database_exists="$(psql_in - -tAc \
  "SELECT 1 FROM pg_database WHERE datname = '${database}'")"
applied=""
if [[ "${database_exists}" == "1" ]]; then
  ledger_exists="$(psql_in "${database}" -tAc \
    "SELECT to_regclass('platform.schema_migrations') IS NOT NULL")"
  if [[ "${ledger_exists}" == "t" ]]; then
    applied="$(psql_in "${database}" -tAc "SELECT version FROM platform.schema_migrations")"
  fi
elif [[ ${dry_run} -eq 0 ]]; then
  psql_in - -c "CREATE DATABASE ${database}"
fi

pending=()
skipped=0
for file in "${migrations[@]}"; do
  version="$(basename "${file}" .sql)"
  if [[ ! "${version}" =~ ^[0-9]{3}_[a-z0-9_]+$ ]]; then
    echo "invalid migration name: ${file}" >&2
    exit 1
  fi
  if grep -qxF "${version}" <<< "${applied}"; then
    skipped=$((skipped + 1))
  else
    pending+=("${file}")
  fi
done

echo "migrations: ${skipped} already applied, ${#pending[@]} pending"
for file in "${pending[@]}"; do
  version="$(basename "${file}" .sql)"
  if [[ ${dry_run} -eq 1 ]]; then
    echo "pending ${version}"
    continue
  fi
  echo "apply ${version}"
  # Not every migration records itself (013 also runs inside the shadow merge
  # without transaction boundaries), so the runner always writes the ledger.
  {
    cat "${file}"
    printf '\nINSERT INTO platform.schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING;\n' \
      "'${version}'"
  } | psql_in "${database}"
done
