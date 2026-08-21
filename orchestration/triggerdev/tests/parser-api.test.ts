import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { enqueueParserRun, getParserRun } from "../src/lib/parser-api.js";

const originalFetch = globalThis.fetch;

function runEnvelope(status = "queued"): Record<string, unknown> {
  return {
    id: "0123456789abcdef0123456789abcdef",
    status,
    attemptPurpose: "manual",
    originOccurrenceId: null,
    recoveryChainId: null,
    sourceIds: ["vicious_syndicate_live_beta"],
    totalSources: 1,
    completedSources: status === "queued" ? 0 : 1,
    failedSources: status === "partial" || status === "failed" ? 1 : 0,
    results:
      status === "queued"
        ? []
        : [
            {
              sourceId: "vicious_syndicate_live_beta",
              state: status === "succeeded" ? "ok" : "fetch_error",
              servingCachedDataset: status === "partial",
              outcome:
                status === "succeeded"
                  ? "fresh_published"
                  : status === "partial"
                    ? "lkg_served"
                    : "failed",
              reasonCode: status === "succeeded" ? "none" : "transport",
              upstreamPending: false
            }
          ]
  };
}

beforeEach(() => {
  process.env.PARSER_CONTROL_BASE_URL = "https://api.example.invalid";
  process.env.PARSER_ORCHESTRATOR_TOKEN = "orchestrator-token-for-tests";
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("enqueue retries transient failures with the same idempotency body", async () => {
  const bodies: string[] = [];
  let calls = 0;
  globalThis.fetch = (async (_input, init) => {
    calls += 1;
    bodies.push(String(init?.body));
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("X-Orchestrator-Key"), "orchestrator-token-for-tests");
    if (calls === 1) {
      return new Response(null, {
        status: 503,
        headers: { "Retry-After": "0" }
      });
    }
    if (calls === 2) {
      throw new TypeError("temporary network failure");
    }
    return Response.json({ run: runEnvelope(), deduplicated: false }, { status: 202 });
  }) as typeof fetch;

  const result = await enqueueParserRun(
    "trigger:run_abc:task",
    { sourceIds: ["vicious_syndicate_live_beta"] },
    "canary"
  );

  assert.equal(result.run.status, "queued");
  assert.equal(calls, 3);
  assert.equal(new Set(bodies).size, 1);
});

test("enqueue forwards recovery correlation without changing it", async () => {
  let body: Record<string, unknown> = {};
  globalThis.fetch = (async (_input, init) => {
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return Response.json(
      {
        run: {
          ...runEnvelope(),
          attemptPurpose: "recovery",
          originOccurrenceId: "schedule:20260820T100000Z",
          recoveryChainId: "chain-1"
        },
        deduplicated: false
      },
      { status: 202 }
    );
  }) as typeof fetch;

  const response = await enqueueParserRun(
    "convergence:chain-1:attempt:2",
    {
      sourceIds: ["vicious_syndicate_live_beta"],
      attemptPurpose: "recovery",
      originOccurrenceId: "schedule:20260820T100000Z",
      recoveryChainId: "chain-1"
    },
    "automatic recovery"
  );

  assert.equal(body.attemptPurpose, "recovery");
  assert.equal(body.originOccurrenceId, "schedule:20260820T100000Z");
  assert.equal(body.recoveryChainId, "chain-1");
  assert.equal(response.run.recoveryChainId, "chain-1");
});

test("endpoint joins root and base-path URLs with exactly one slash", async () => {
  const cases = [
    {
      base: "https://api.example.invalid",
      expected: "https://api.example.invalid/admin/orchestrator/parser-runs"
    },
    {
      base: "https://api.example.invalid/parser-control/",
      expected:
        "https://api.example.invalid/parser-control/admin/orchestrator/parser-runs"
    }
  ];

  for (const row of cases) {
    process.env.PARSER_CONTROL_BASE_URL = row.base;
    let requestedUrl = "";
    globalThis.fetch = (async (input) => {
      requestedUrl = String(input);
      return Response.json(
        { run: runEnvelope(), deduplicated: false },
        { status: 202 }
      );
    }) as typeof fetch;

    await enqueueParserRun(
      "trigger:run_url:task",
      { sourceIds: ["vicious_syndicate_live_beta"] },
      "canary"
    );

    assert.equal(requestedUrl, row.expected);
  }
});

test("enqueue does not retry a validation response", async () => {
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    return Response.json({ detail: "invalid" }, { status: 422 });
  }) as typeof fetch;

  await assert.rejects(
    enqueueParserRun(
      "trigger:run_invalid:task",
      { sourceIds: ["vicious_syndicate_live_beta"] },
      "canary"
    ),
    /HTTP 422/
  );
  assert.equal(calls, 1);
});

test("run status rejects malformed terminal counters", async () => {
  globalThis.fetch = (async () =>
    Response.json({
      run: {
        ...runEnvelope("succeeded"),
        completedSources: 0,
        failedSources: 0
      }
    })) as typeof fetch;

  await assert.rejects(
    getParserRun("0123456789abcdef0123456789abcdef"),
    /invalid run response/
  );
});

test("successful response with truncated JSON is retried", async () => {
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    if (calls === 1) {
      return new Response("{", {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
    return Response.json({ run: runEnvelope("succeeded") });
  }) as typeof fetch;

  const run = await getParserRun("0123456789abcdef0123456789abcdef");

  assert.equal(run.status, "succeeded");
  assert.equal(calls, 2);
});

test("run status accepts bounded exact paid usage", async () => {
  const envelope = runEnvelope("succeeded");
  const [result] = envelope.results as Array<Record<string, unknown>>;
  assert.ok(result);
  result.paidRequests = 1;
  result.paidCostMicrousd = 290;
  result.paidUsageExact = true;
  globalThis.fetch = (async () => Response.json({ run: envelope })) as typeof fetch;

  const run = await getParserRun("0123456789abcdef0123456789abcdef");

  assert.equal(run.results[0]?.paidRequests, 1);
  assert.equal(run.results[0]?.paidCostMicrousd, 290);
  assert.equal(run.results[0]?.paidUsageExact, true);
});

test("run status rejects exact paid usage without an exact cost", async () => {
  const envelope = runEnvelope("succeeded");
  const [result] = envelope.results as Array<Record<string, unknown>>;
  assert.ok(result);
  result.paidRequests = 1;
  result.paidUsageExact = true;
  globalThis.fetch = (async () => Response.json({ run: envelope })) as typeof fetch;

  await assert.rejects(
    getParserRun("0123456789abcdef0123456789abcdef"),
    /invalid run response/
  );
});
