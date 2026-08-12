# Web panel

This directory is the source of the authenticated database panel served at
`https://api.kolodahearthstone.com/`. Only GitHub user `Zulut30` is allowed to
open it. The public REST compatibility surface is under `/api/v1`, while the
central GraphQL API is under `/v1/`.

Runtime-only files are deliberately excluded from Git:

- `config.php` contains the database connection settings;
- `uploads/` contains generated media;
- `var/` contains caches and synchronization state;
- GitHub OAuth and API-token credentials live below `/var/lib/koloda/`.

Production layout:

| Path | Purpose |
|---|---|
| `/srv/api-kolodahearthstone/panel/releases/<id>` | immutable code release |
| `/srv/api-kolodahearthstone/panel/current` | active release symlink |
| `/srv/api-kolodahearthstone/panel-data/uploads` | persistent media |
| `/srv/api-kolodahearthstone/panel-data/var` | persistent cache and job state |
| `/etc/api-kolodahearthstone/panel-config.php` | private database config |

Deploy from the repository root with `sudo scripts/deploy-panel.sh`. The script
creates a new release and switches `current` atomically. It never copies or
deletes the persistent data directories.
