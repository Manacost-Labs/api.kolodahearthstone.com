import { logger, metadata, task, wait } from "@trigger.dev/sdk";

import { parserControlConfig } from "../lib/env.js";
import {
  enqueueParserRun,
  getParserRun,
  type ParserSelection
} from "../lib/parser-api.js";

export interface ParserJobPayload extends ParserSelection {
  reason?: string;
}

export interface ParserJobResult {
  outcome: "fresh" | "degraded";
  localParserRunId: string;
  status: "succeeded" | "partial";
  totalSources: number;
  completedSources: number;
  failedSources: number;
}

function validateSelection(payload: ParserJobPayload): void {
  const sourceIds = payload.sourceIds ?? [];
  const sectionIds = payload.sectionIds ?? [];
  if (sourceIds.length + sectionIds.length === 0) {
    throw new Error("At least one sourceId or sectionId is required");
  }
  if (sourceIds.length > 100 || sectionIds.length > 30) {
    throw new Error("Parser selection is too large");
  }
  for (const value of [...sourceIds, ...sectionIds]) {
    if (!/^[A-Za-z0-9_.:-]{1,120}$/.test(value)) {
      throw new Error("Parser selection contains an invalid identifier");
    }
  }
}

export async function runLocalParserJob(
  payload: ParserJobPayload,
  triggerRunId: string,
  taskId: string
): Promise<ParserJobResult> {
  validateSelection(payload);
  const { runTimeoutMs } = parserControlConfig();
  const deadline = Date.now() + runTimeoutMs;
  const requestId = `trigger:${triggerRunId}:${taskId}`;
  const created = await enqueueParserRun(
    requestId,
    payload,
    payload.reason?.slice(0, 500) || `Trigger.dev task ${taskId}`
  );
  let run = created.run;

  metadata.set("localParserRunId", run.id);
  metadata.set("deduplicated", created.deduplicated);
  logger.info("Local parser run accepted", {
    localParserRunId: run.id,
    deduplicated: created.deduplicated,
    totalSources: run.totalSources
  });

  while (run.status === "queued" || run.status === "running") {
    if (Date.now() >= deadline) {
      throw new Error(`Local parser run ${run.id} exceeded its wall-clock deadline`);
    }
    await wait.for({ seconds: 30 });
    run = await getParserRun(run.id);
    metadata.set("completedSources", run.completedSources);
    metadata.set("failedSources", run.failedSources);
  }

  metadata.set("localParserStatus", run.status);
  if (run.status === "failed") {
    throw new Error(`Local parser run ${run.id} failed`);
  }
  if (run.status === "partial") {
    logger.warn("Local parser run completed with usable degraded data", {
      localParserRunId: run.id,
      failedSources: run.failedSources,
      totalSources: run.totalSources
    });
    return {
      outcome: "degraded",
      localParserRunId: run.id,
      status: "partial",
      totalSources: run.totalSources,
      completedSources: run.completedSources,
      failedSources: run.failedSources
    };
  }
  return {
    outcome: "fresh",
    localParserRunId: run.id,
    status: "succeeded",
    totalSources: run.totalSources,
    completedSources: run.completedSources,
    failedSources: run.failedSources
  };
}

export const parserJob = task({
  id: "hearthstone-parser-run",
  queue: { concurrencyLimit: 1 },
  retry: { maxAttempts: 2 },
  run: async (payload: ParserJobPayload, { ctx }) =>
    runLocalParserJob(payload, ctx.run.id, ctx.task.id)
});
