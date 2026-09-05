# Parser quality implementation

This is the delivery ledger for the parser/ParsesUnix quality plan approved on
2026-09-04. A merged source change is not evidence of a production deployment or
of the long-term freshness SLO. Paid refreshes, production repairs, and release
checkpoints require explicit authorization. The user has now authorized `main`
integration and production rollout of the verified bounded quality patch.
No additional paid collection or production data repair is included.

## Integrated engine patch 0.10.2

The API dependency pins the reproducibly built ParsesUnix 0.10.2 wheel and
SHA-256 `3ea80ec34a77d8c2d674c3add4918e5a78477aa6323c8042c832d89d803055ed`.
It includes T11–T16: JSON/staging/schema fixes, per-class required fields,
critical-conflict publication guards and real record/identity acceptance checks.
Quorum-only legacy profiles remain supported. The embedded API transport does
not instantiate the standalone engine DatasetStore, so its additive staging
columns do not trigger a production dataset migration here.

The [engine release runbook](https://github.com/Manacost-Labs/ParsesUnix/blob/main/docs/operations/parser-quality-release.md)
defines the artifact, contracts, exact-image rollout and rollback. Production
uses the smoke-tested immutable image ID, not a subsequent rebuild. The old
actual running image is retained; host source, runtime image and dependency
version are checked separately. No schedules, active source cohort or spending
limits are expanded. Deployment and CI outcomes are recorded with the release,
not inferred from this dependency declaration.

CI now invokes the same `make check` as local verification, including panel,
platform, documentation and SDK gates. Separate SDK packaging and browser/core
matrix coverage remain in their relevant workflows. This release is not full
closure of the remaining plan below.

## Baseline and ownership

- API candidate starts from GitHub `216135c`, not the stale local `3721278`.
- ParsesUnix starts from `f0c2aaa`, package `web-scraper-core` 0.10.1.
- API work is isolated on `fix/parser-quality-20260904`. Existing main and other
  worktrees are protected and were not modified.
- Runtime image observed: `sha256:2785207f4fd80dc8a76db7a63271c5308fd7875b730cb72c05cbe9a34f38496e`.
  Source SHA labels are absent; this digest is not claimed to prove a build commit.
- Runtime `main.py`, `fetcher.py`, `sources.py`, `structured.py`,
  `source_validators.py`, `storage.py`, and `parsesunix_contracts.py` match the
  selected GitHub baseline byte-for-byte. No runtime-only backport is necessary
  for these files. This does not establish equivalence of every image file.
- API and one-off HSGuru catalog refresh containers share the dataset mount.
  Scheduled jobs also run the API image. Ledger recovery must account for live
  processes, not assume that a second process means a previous one crashed.
- JSON publication, legacy SQLite indexing, PostgreSQL import/shadow sync, and
  panel readers are separate paths. Complete reader/version parity remains T25/T26.
- T02 date-test adjustments and T03 dependency updates already exist in the new
  baseline. Their full plan acceptance is not inferred merely from their diff.

## Delivered: T04 shared post-patch page threshold

Both legacy page checks and ParsesUnix response contracts now use the same
effective HTML minimum from the existing captured publication policy. The
stable-validation context still overrides early mode. Sample-size relaxation
does not replace semantic validation, and the existing changed-policy publication
guard is retained.

Verification:

- Regression RED: 7 failures / 13 passes before the threshold fix.
- Focused GREEN: 96 tests, including existing publication-race checks.
- Final threshold cases: 20 passed, including 1999/2000/2001,
  24999/25000/25001 bytes, realistic challenge pages, stable override, policy
  changes, and a source with no early threshold.
- API `make check`: 1770 Python tests and 213 subtests passed; panel, platform,
  documentation, TypeScript SDK, and Actionlint passed. This full run preceded
  the final additional no-early-threshold test, which passed in the focused run.
- Local C# checks were skipped by the project gate because `dotnet` is absent;
  CI verification is still required before claiming C# coverage.
- `make security`: Gitleaks and OSV reported no issues in their supported scan;
  the scanner explicitly excluded 56 local/unscannable packages.
- Luna Context Scout and independent micro-review executed. Review corrected a
  test that mistook bare Cloudflare markers for a real challenge. No broad
  marker blacklist was added to ParsesUnix.

## Delivered: T05 per-row matchup validation

Every HSGuru matchup now requires finite winrate in `[0, 100]`, two valid distinct
archetype names, and a unique pair. Non-object rows are reported rather than
silently discarded. Early mode cannot weaken these rules. Malformed raw cells
are retained as evidence instead of being converted to apparent percentages.
Explained upstream-empty/self cells retain the existing omission accounting.

- Commits: `e52ffc3` (T04), `4c1e5d2` (T05).
- T05 RED: 37 failures / 20 passes. Final focused GREEN: 197 tests plus 8 subtests,
  including mixed valid/invalid HTML through the parser and publication validator.
- Integrated final `make check`: 1834 Python tests and 213 subtests passed;
  panel (44 Python/40 JavaScript tests and PHP/shell checks), platform, docs,
  TypeScript SDK, and Actionlint passed. Local C# remains unverified, as above.
- Eleven pre-existing Ruff findings in `app/structured.py` are unchanged; the
  updated validator and new tests pass Ruff. No broad formatting cleanup was made.
- T03 verification: clean orchestrator installation, TypeScript check, 10 tests,
  and dependency audit all passed with no reported vulnerabilities. The fixed
  versions were inherited from `216135c`, not authored by this implementation.
- Independent Luna review found no remaining required T05 findings.

## Remaining plan checkpoints

T00 inventory/provenance is partial until all readers and build metadata are
accounted for. T01 regressions are added alongside each corresponding fix, not
as an intentionally failing batch.

| Tasks | Scope | Status |
| --- | --- | --- |
| T02 | Deterministic time boundaries | Existing fixes; expanded boundary acceptance pending |
| T03 | Dependency verification | Completed against inherited fixes |
| T05 | Per-row HSGuru matchup validity | Delivered and verified |
| T06–T10 | Context, source JSON shapes, upstream freshness, all 99 contracts | Pending |
| T11–T18 | Extraction, drift, required fields, quorum, acceptance, pagination, XPath | In progress in ParsesUnix |
| T19–T23 | Truthful telemetry, coordinated budget, deadlines, quality feedback/retries | Pending |
| T24–T27 | Versioned publication, durable indexes, consistent reads, shadow sync | Pending |
| T28–T29 | Operator panel and targeted dataset recovery | Pending |
| T30–T31 | Reproducible engine release, integration pin, SDK/docs contract | Bounded 0.10.2 patch prepared; full-plan dependencies still pending |
| T32–T33 | Offline/shadow comparison and staged production release | Bounded patch authorized; full all-source shadow and long-term observation remain pending |

## Verification policy

Profile: `data`. Applied workflow: using-agent-skills, task breakdown,
incremental implementation, TDD, context engineering, git workflow, and gated
team review. CodeGraph and GitHub produced navigation/source evidence. Serena
timed out; no Serena result is claimed. Browser/performance/production API tests
are not substitutes for offline parser tests and are not applicable to T04.
No schema migration, secret/auth change, production write, or paid fetch was made.

The canonical skill catalog lacks the referenced TDD `writing-good-tests.md`
and generic definition-of-done files. The explicit server/project testing and
review contract was used; those missing reference files were not claimed read.
