# Daily game-change audit

The production host runs `hs-data-api-docker-game-change-audit.timer` every day
at 11:15 Europe/Warsaw, after the normal data refresh and patch-catalog refresh.
It never replaces a valid dataset with unverified upstream data. When a new
patch version is confirmed, it atomically enables a four-day `early` policy in
parser-control before advancing the patch baseline. A failed policy write keeps
the old baseline, so the next audit retries activation.

While that policy is active, the recurring post-patch timer refreshes every
operational early-policy scrape source at 00:20, 05:20, 10:20, 15:20 and 20:20
Europe/Warsaw. Outside the bounded window the command exits successfully before
making provider requests. Section switches in parser-control are still honored.

The audit compares four independent surfaces:

1. The current patch from Blizzard patch notes plus the wiki.gg patch catalog.
2. The enUS and ruRU HearthstoneJSON card catalogs. Relevant gameplay fields
   are fingerprinted so additions, removals, balance changes, localization gaps
   and missing Battlegrounds premium/golden links are visible.
3. The previous 36 hours of wiki.gg main-namespace changes, with patch, card,
   Arena, Battlegrounds, quest, Titan, hero and trinket pages highlighted.
4. Representative production feeds for Standard, Wild, Arena, Battlegrounds
   heroes/minions/spells/trinkets and both strategy providers (HSReplay and
   Firestone). Stale, failed and cached-after-failure sources require attention.

Reports are written atomically to:

- `/var/lib/hs-data-api/audits/game-change-latest.json`;
- `/var/lib/hs-data-api/audits/game-change-YYYY-MM-DD.json`;
- `/var/lib/hs-data-api/audits/game-change-baseline.json`.

The service exits non-zero and sends the configured Telegram alert when a new
patch/card change is found or a critical feed is unhealthy. The upstream card
baseline is still advanced after a complete fetch, so the report distinguishes
new changes from persistent site-source failures on the following day.

Manual verification:

```bash
docker compose -f /srv/hs-data-api/docker-compose.yml run --rm api \
  python -m app.cli game-change-audit
systemctl status hs-data-api-docker-game-change-audit.timer
journalctl -u hs-data-api-docker-game-change-audit.service -n 100 --no-pager
```

When a patch is detected, refresh and verify both strategy tabs separately.
HSReplay and Firestone are independent catalogs; a fresh Firestone result does
not prove that the HSReplay strategy view is current.
