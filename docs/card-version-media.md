# Card version images

The existing `/api/v1/card-history/{entity}/{card_id}` response adds a `media`
object to each observed version. Its keys are image field names, such as
`card_image`, `image_url`, and `golden_image`. Each record includes `status`,
`reason`, `archive_url`, `sha256`, `byte_size`, `mime_type`, `evidence`, and
`captured_at`. Empty media is an empty object. Pagination remains bounded.

Only `status: archived` refers to stored bytes. The URL is content addressed:
`/uploads/card-history-media/{sha256}.{extension}`. Existing archived records
and files are never overwritten. The panel compares available archived images
and displays an explicit missing-image state for unverified versions.

`captured_after_observation` means the bytes were captured after the version was
observed. It does not prove what a mutable upstream URL served earlier.
`immutable_source_hash` verifies bytes against a hash embedded in a historical
source filename. Old mutable URLs are marked `historical_mutable_url` instead
of being assigned today's image. Missing files, invalid images and unsuccessful
requests remain explicitly unverified or failed. The API's
`coverage.image_bytes_archived` stays false; `archived_images_on_page` reports
the bounded page's actual archived count. History before the first observation
remains unknown.

## Operational capture

The worker's default is a read-only plan. Installation and capture require
explicit flags:

```sh
python panel/scripts/archive_card_history_media.py --limit 20
python panel/scripts/archive_card_history_media.py --apply --install --limit 20
```

`--remote` explicitly permits requests to an allowlist of public image hosts;
redirects are validated and inherited proxy settings are disabled. Private
credentials are not forwarded. Size and pixel limits apply before archiving.
Current-version identity is rechecked under a database lock before recording.

The prepared systemd unit prioritizes up to 20 new current versions, scans up to
50 backlog versions with a durable locked cursor, and retries up to 10 failed
versions after six hours. The timer invokes it one minute after completion.
The unit, table and timer must be installed as part of an approved deployment;
adding source files alone does not activate capture.

PostgreSQL migration `014_card_version_media.sql` supports the existing catalog
mirror. Migration `013_hsguru_deck_radar.sql` and the explicit radar shadow
fragment preserve radar observations, snapshots and mutable event statuses in
the mirror's transaction.

## Deployment preservation

`scripts/stage-chat-gaps-runtime.py` prepares a new candidate from the active
PHP release and pinned API files. It keeps private configuration/data symlinks,
live artwork recipes, the API's tile routes, Lab ingestion and incremental
analytics synchronization. Changed anchors stop staging. It never activates a
release or changes a database. Preserve the original API image, release
pointers, platform script and catalog backups before activation.

The source inventory now uses versioned metadata indexes rather than decoding
every dataset for each request. Completeness is unknown without an independent
expected population. The SLO objective is 99.9%; setting the objective does not
establish that it has been achieved.
