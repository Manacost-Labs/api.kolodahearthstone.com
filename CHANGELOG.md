# Changelog

## Unreleased

- Accepted title-less structured API snapshots during last-known-good
  validation, so a temporary HSReplay premium-session failure no longer turns
  a valid Battlegrounds hero dataset into a hard parser error.
- Calibrated the Standard Legend 24-hour card contract for its lower sample
  size while retaining field-fill and regression protection.
- Made the unified HSGuru matrix use Scrape.do before Firecrawl, and exposed
  logical/base/fresh/cached slice counts to parser monitoring.
- Made the four HSGuru deck catalogs refresh independently so one upstream
  failure no longer prevents the remaining format/rank datasets from updating.
- Allowed the unified HSGuru matrix to publish a clearly marked partial
  snapshot when one current-format catalog can be restored from the same
  patch's last-known-good data.
- Made a successful Battlegrounds hero-details JSON refresh update the
  compatible hero index too, removing its dependency on a separate premium
  HTML session for freshness.
