#!/usr/bin/env bash
# Apply every platform migration to a throwaway PostgreSQL, then run the
# runner again and require that it is a no-op. Needs docker; no credentials.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
runner="${root}/platform/scripts/apply-migrations.sh"
image="$(awk '/image: postgres:/ {print $2; exit}' "${root}/platform/postgres/docker-compose.yml")"
container="hs-platform-migrations-test-$$"
docker_bin="${DOCKER:-docker}"

cleanup() { "${docker_bin}" rm -f "${container}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

"${docker_bin}" run -d --name "${container}" \
  -e POSTGRES_USER=platform -e POSTGRES_PASSWORD=ci-only -e POSTGRES_DB=platform \
  "${image}" >/dev/null
for _ in $(seq 1 60); do
  # pg_isready also succeeds against the temporary init-phase server, so
  # probe with a real query over TCP, which only the final server accepts.
  if "${docker_bin}" exec "${container}" psql -h 127.0.0.1 -U platform -d platform -tAc 'SELECT 1' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

export PLATFORM_DOCKER="${docker_bin}" PLATFORM_PSQL_CONTAINER="${container}"
psql_test() { "${docker_bin}" exec -i "${container}" psql -X -q -v ON_ERROR_STOP=1 -U platform "$@"; }

expected="$(find "${root}/platform/sql" -maxdepth 1 -name '[0-9][0-9][0-9]_*.sql' -exec basename {} .sql \; | LC_ALL=C sort)"
duplicates="$(cut -c1-3 <<< "${expected}" | uniq -d)"
if [[ -n "${duplicates}" ]]; then
  echo "FAIL: duplicate migration numbers: ${duplicates}" >&2
  exit 1
fi
count="$(wc -l <<< "${expected}")"

dry="$("${runner}" --dry-run)"
grep -q "0 already applied, ${count} pending" <<< "${dry}" || { echo "FAIL: dry-run on empty server: ${dry}" >&2; exit 1; }
if psql_test -d platform -tAc "SELECT 1 FROM pg_database WHERE datname = 'hs_data'" | grep -q 1; then
  echo "FAIL: --dry-run created the database" >&2
  exit 1
fi

# Tables pgloader creates in production before any migration runs.
psql_test -d platform -c 'CREATE DATABASE hs_data'
psql_test -d hs_data < "${root}/platform/tests/fixtures/pgloader-baseline.sql" >/dev/null

"${runner}"
recorded="$(psql_test -d hs_data -tAc 'SELECT version FROM platform.schema_migrations ORDER BY version')"
if [[ "${recorded}" != "${expected}" ]]; then
  echo "FAIL: ledger does not match migration files" >&2
  diff <(echo "${expected}") <(echo "${recorded}") >&2 || true
  exit 1
fi

second="$("${runner}")"
if [[ "${second}" != "migrations: ${count} already applied, 0 pending" ]]; then
  echo "FAIL: second run was not a no-op: ${second}" >&2
  exit 1
fi

echo "platform migrations: ${count} applied from scratch, re-run is a no-op"
