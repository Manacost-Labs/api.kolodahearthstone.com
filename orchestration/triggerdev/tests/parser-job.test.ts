import assert from "node:assert/strict";
import { test } from "node:test";

import { parserRequestId } from "../src/trigger/parser-job.js";

test("recovery request ID remains stable across Trigger retries and runs", () => {
  const payload = {
    sourceIds: ["vicious_syndicate_live_beta"],
    attemptPurpose: "recovery" as const,
    recoveryChainId: "chain-1",
    originOccurrenceId: "schedule:20260820T100000Z",
    attemptNumber: 2
  };

  assert.equal(
    parserRequestId(payload, "trigger-run-a", "task-a"),
    "convergence:chain-1:attempt:2"
  );
  assert.equal(
    parserRequestId(payload, "trigger-run-b", "task-b"),
    "convergence:chain-1:attempt:2"
  );
});

test("manual request ID remains scoped to the Trigger run", () => {
  assert.equal(
    parserRequestId(
      { sourceIds: ["vicious_syndicate_live_beta"] },
      "trigger-run-a",
      "task-a"
    ),
    "trigger:trigger-run-a:task-a"
  );
});
