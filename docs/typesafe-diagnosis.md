# TypeSafe failure diagnosis through OpenRouter

The optional TypeSafe adapter classifies an already rejected parser candidate.
It uses `POST https://openrouter.ai/api/v1/systemone` and the existing
`HS_OPENROUTER_API_KEY` configuration. It does not need a TypeSafe account or SDK.
The default remains the existing OpenRouter chat diagnosis.

## Configuration

```dotenv
HS_AI_REVIEW_ENABLED=true
HS_AI_REVIEW_MODE=observe
HS_AI_REVIEW_DIAGNOSE_FAILURES=true
HS_AI_REVIEW_DIAGNOSIS_PROVIDER=typesafe
HS_AI_REVIEW_DIAGNOSIS_MODEL=typesafe/jev-1.13
HS_AI_REVIEW_DIAGNOSIS_MIN_CONFIDENCE=0.95
```

Apply these settings through the normal deployment configuration. The code change
does not edit runtime configuration or enable outbound calls by itself. An empty
diagnosis model selects `typesafe/jev-1.13` for TypeSafe, or the existing
`HS_AI_REVIEW_MODEL` for the `openrouter` chat provider. Candidate reviews continue
to use `HS_AI_REVIEW_MODEL` and the chat endpoint.

To return to chat diagnosis, set `HS_AI_REVIEW_DIAGNOSIS_PROVIDER=openrouter` and
clear `HS_AI_REVIEW_DIAGNOSIS_MODEL`. To disable failure diagnosis, set
`HS_AI_REVIEW_DIAGNOSE_FAILURES=false`.

## Behavior and boundaries

The existing evidence builder emits bounded validation results, aggregates and
closed-set codes. It excludes raw upstream content, cookies, credentials and URLs.
Only this prepared evidence is sent. The adapter therefore cannot identify a new
page-level signal that the evidence builder did not capture.

One Choice question selects a failure domain or `expected_post_patch_sparse`.
Python maps a failure domain to an
advisory action, such as `refresh_auth`, `update_parser`, or `retry_existing_route`.
It does not execute the action, approve publication, alter freshness checks,
change the rejected candidate's terminal outcome, or quarantine successful data.
This remains true even when candidate reviews use quarantine mode.

The expected-sparsity answer becomes a `healthy` diagnostic with evidence code
`post_patch_sparse_expected` only when local evidence confirms an active early
policy, the regression-rejection stage after candidate validation, matching
source identity, passing contract and semantic checks, a nonempty row-count drop,
and no decline in filled metrics per row. All other expected-sparsity answers
become `inconclusive`. A healthy diagnostic does not publish or retry a rejected
candidate. Existing deterministic early-publication policy still decides whether
to accept a smaller dataset as provisional.

Post-patch phase is available to diagnosis before a candidate has publication
metadata. Empty publication metadata no longer hides an existing candidate phase.
Expired policy, unsupported sources, stale/invalid candidates and falling field
coverage cannot be excused by the expected-sparsity answer.

Unknown or below-threshold answers become `inconclusive` with action `none`.
The initial 0.95 threshold is conservative configuration, not a measured accuracy
guarantee. TypeSafe confidence is derived from its probability distribution; tune
the threshold on labeled examples rather than treating it as empirical accuracy.

The existing deferred diagnosis lane, timeout, response-size cap, concurrency,
per-refresh budget, retry policy and circuit breaker also apply to TypeSafe.
Malformed answers, HTTP failures and timeouts produce diagnostic errors and do not
change publication. The adapter sends the documented System One request fields;
chat-specific routing parameters and response-healing plugins are not forwarded.

Telemetry records `prompt_version=typesafe-diagnosis-v2`, the configured model,
provider, input/output tokens, OpenRouter cost, confidence and the domain
probabilities. The existing classification/domain/action fields remain compatible.
The configured model is recorded instead of accepting arbitrary model text from
the remote response.

## Verification and rollout

Unit and HTTP integration tests use `httpx.MockTransport`; no paid API calls are
made by tests. Run:

```sh
.venv/bin/python -m pytest -q tests/test_typesafe_diagnosis.py tests/test_typesafe_post_patch.py tests/test_ai_review.py tests/test_post_patch_policy.py
make check
```

Before relying on recommendations, compare rules, chat diagnosis and Jev on the
same labeled rejected candidates. Measure failure-domain precision, incorrect
high-confidence answers, unknown rate, latency and cost. Observe fresh-update
success rate and recovery time after operational use; this integration alone
does not establish an improvement in either metric.

References:

- [OpenRouter System One API and TypeSafe SDK](https://openrouter.ai/docs/guides/community/typesafe-sdk)
- [TypeSafe confidence](https://docs.typesafe.ai/confidence)
- [Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
