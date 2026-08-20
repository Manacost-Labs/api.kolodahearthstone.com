#!/usr/bin/env bash
# Backward-compatible shortcut for the zero-cost HSGuru shadow canary.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/configure-parsesunix-hsguru.sh" shadow "$@"
