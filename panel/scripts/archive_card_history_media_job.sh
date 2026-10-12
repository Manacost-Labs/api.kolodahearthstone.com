#!/usr/bin/env bash
set -euo pipefail

# The systemd supervisor holds the shared platform lock across all three passes.
# The worker runs as koloda; root is used only to acquire the private lock.
media_python="${CARD_MEDIA_PYTHON:-/opt/wiki-hs-parser/.venv/bin/python}"
media_panel="${CARD_MEDIA_PANEL_ROOT:-/srv/api-kolodahearthstone/panel/current}"
media_state="${CARD_MEDIA_STATE:-/srv/api-kolodahearthstone/panel-data/var/card-history-media-state.json}"

"$media_python" "$media_panel/scripts/archive_card_history_media.py" --apply --remote --newest --limit 20
"$media_python" "$media_panel/scripts/archive_card_history_media.py" --apply --remote --limit 50 --state "$media_state"
"$media_python" "$media_panel/scripts/archive_card_history_media.py" --apply --remote --retry --limit 10
