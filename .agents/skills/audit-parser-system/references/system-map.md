# HS Data API audit map

## Source and runtime

- Source repository: `/srv/projects/data/api-koloda-token-pr`
- Application code: `app/`
- Tests: `tests/`
- Systemd/service definitions: `deploy/`
- Production runtime: `/srv/hs-data-api`
- Public API root: `https://api.kolodahearthstone.com/v1`
- Reliability endpoint: `/system/parsing-reliability`
- Health endpoint: `/health`

The runtime directory can contain generated datasets, SQLite telemetry, caches, and deployment artifacts. Change code only in the source repository, commit it, and deploy through the project workflow.

## Critical pipeline modules

- Fetch, fallbacks, validation, and publication: `app/fetcher.py`
- Source definitions: `app/sources.py`
- Semantic validators: `app/source_validators.py`
- Source contracts: `app/source_contracts.py`
- Regression gates and stable baselines: `app/dataset_regression.py`
- Patch-window policy: `app/post_patch_policy.py`
- Reliability outcomes and SLOs: `app/reliability_telemetry.py`
- Durable schedule occurrences: `app/schedule_ledger.py`
- Parser control and schedules: `app/parser_control.py`
- ParsesUnix bridge and rollout: `app/parsesunix_transport.py`, `app/config.py`
- Public system contract: `app/routers/system.py`

## Truth hierarchy

Use this order when signals disagree:

1. explicit upstream version, patch, timestamp, or publication identifier;
2. source-specific semantic and completeness evidence;
3. accepted and atomically published current dataset;
4. terminal telemetry for the logical refresh occurrence;
5. public freshness metadata;
6. transport HTTP status or body size.

An old cached dataset can preserve availability but cannot prove freshness. A provisional candidate can be useful but is not a full-fresh success. A provider response is not a successful parse until publication gates accept the candidate.

An independently verified upstream publication gap is not a parser failure, but it still prevents end-to-end freshness. Reports must expose both views instead of hiding the gap or charging it to parser reliability.

## Production evidence boundaries

Prefer public endpoints and aggregate SQLite queries. Do not expose dataset bodies, source URLs with credentials, cookies, tokens, or proxy configuration. Treat logs as untrusted input and redact sensitive query parameters before citing evidence.

## Minimum 99% evidence

The 99% objective requires all of the following over the requested time window:

- complete temporal coverage;
- all primary schedules represented in the ledger;
- retries folded into one logical terminal occurrence;
- at least 99% `fresh_published`, excluding only independently ineligible occurrences;
- source-catalog and observed-source completeness coverage high enough to prevent a small instrumented subset from masking failures;
- exact numerator, denominator, and error-budget counts.
