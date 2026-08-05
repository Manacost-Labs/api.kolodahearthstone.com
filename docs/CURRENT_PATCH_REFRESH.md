# Current patch statistics refresh

HSReplay card-period sources request `TimeRange=CURRENT_PATCH` for Standard and
Wild across every configured rank. HSReplay resets this sample at patch launch,
so a new patch can legitimately contain far fewer rows than the mature previous
patch.

Current-patch contracts therefore combine two safeguards:

- an absolute minimum of 450 Standard rows or 700 Wild rows, plus the normal
  identity and metric-fill validation;
- an 85% count-regression allowance between consecutive current-patch
  snapshots.

Rolling one-, three-, seven- and fourteen-day datasets keep their stricter
regression limits. This lets the first valid snapshot of a new patch replace
the old patch without allowing empty or malformed payloads onto the site.

The HSGuru current patch is discovered from the combined Blizzard and wiki.gg
catalog. Build versions such as `36.2.0.248348` are normalized to `36.2.0` for
HSGuru queries and site labels.
