import { parserControlConfig } from "./env.js";

export type ParserRunStatus = "queued" | "running" | "succeeded" | "partial" | "failed";

export interface ParserRun {
  id: string;
  status: ParserRunStatus;
  attemptPurpose: "manual" | "recovery";
  originOccurrenceId: string | null;
  recoveryChainId: string | null;
  sourceIds: string[];
  completedSources: number;
  failedSources: number;
  totalSources: number;
  results: Array<{
    sourceId: string;
    state: string;
    servingCachedDataset: boolean;
  }>;
}

export interface ParserSelection {
  sourceIds?: string[];
  sectionIds?: string[];
  attemptPurpose?: "manual" | "recovery";
  originOccurrenceId?: string;
  recoveryChainId?: string;
}

function endpoint(path: string): URL {
  const { baseUrl } = parserControlConfig();
  const requestUrl = new URL(baseUrl);
  const basePath = requestUrl.pathname.replace(/\/+$/, "");
  const endpointPath = path.replace(/^\/+/, "");
  requestUrl.pathname = `${basePath}/${endpointPath}`;
  return requestUrl;
}

async function parserRequest(path: string, init?: RequestInit): Promise<unknown> {
  const { token } = parserControlConfig();
  const requestUrl = endpoint(path);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  headers.set("Content-Type", "application/json");
  headers.set("X-Orchestrator-Key", token);

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    let response: Response;
    try {
      response = await fetch(requestUrl, {
        ...init,
        redirect: "error",
        signal: AbortSignal.timeout(20_000),
        headers
      });
    } catch {
      if (attempt === 3) {
        throw new Error("Parser control API network request failed after 3 attempts");
      }
      await sleep(retryDelayMs(undefined, attempt));
      continue;
    }

    if (response.ok) {
      try {
        return await response.json();
      } catch {
        if (attempt === 3) {
          throw new Error(
            "Parser control API returned invalid JSON after 3 attempts"
          );
        }
        await sleep(retryDelayMs(undefined, attempt));
        continue;
      }
    }
    if (!isRetryableStatus(response.status) || attempt === 3) {
      throw new Error(`Parser control API returned HTTP ${response.status}`);
    }
    await sleep(retryDelayMs(response.headers.get("Retry-After"), attempt));
  }
  throw new Error("Parser control API request failed");
}

function isRetryableStatus(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

function retryDelayMs(retryAfter: string | undefined | null, attempt: number): number {
  if (retryAfter) {
    const seconds = Number(retryAfter);
    if (Number.isFinite(seconds) && seconds >= 0) {
      return Math.min(5_000, Math.round(seconds * 1_000));
    }
    const at = Date.parse(retryAfter);
    if (Number.isFinite(at)) {
      return Math.min(5_000, Math.max(0, at - Date.now()));
    }
  }
  return Math.min(2_000, 200 * 2 ** (attempt - 1) + Math.floor(Math.random() * 101));
}

async function sleep(milliseconds: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function isParserRun(value: unknown): value is ParserRun {
  if (!value || typeof value !== "object") return false;
  const run = value as Partial<ParserRun>;
  const statuses: ParserRunStatus[] = [
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed"
  ];
  if (
    typeof run.id !== "string" ||
    !/^[a-f0-9]{32}$/.test(run.id) ||
    !statuses.includes(run.status as ParserRunStatus) ||
    (run.attemptPurpose !== "manual" && run.attemptPurpose !== "recovery") ||
    (run.originOccurrenceId !== null &&
      (typeof run.originOccurrenceId !== "string" ||
        !/^[A-Za-z0-9_.:-]{1,160}$/.test(run.originOccurrenceId))) ||
    (run.recoveryChainId !== null &&
      (typeof run.recoveryChainId !== "string" ||
        !/^[A-Za-z0-9_.:-]{1,160}$/.test(run.recoveryChainId))) ||
    !Array.isArray(run.sourceIds) ||
    !run.sourceIds.every(
      (sourceId) =>
        typeof sourceId === "string" && /^[A-Za-z0-9_.:-]{1,120}$/.test(sourceId)
    ) ||
    !isCount(run.totalSources) ||
    run.totalSources !== run.sourceIds.length ||
    !isCount(run.completedSources) ||
    run.completedSources > run.totalSources ||
    !isCount(run.failedSources) ||
    run.failedSources > run.completedSources ||
    !Array.isArray(run.results) ||
    run.results.length > run.completedSources ||
    !run.results.every(isParserResult)
  ) {
    return false;
  }

  if (
    (run.attemptPurpose === "recovery" && run.recoveryChainId === null) ||
    (run.attemptPurpose !== "recovery" &&
      (run.recoveryChainId !== null || run.originOccurrenceId !== null))
  ) {
    return false;
  }

  const terminal =
    run.status === "succeeded" || run.status === "partial" || run.status === "failed";
  if (terminal && run.completedSources !== run.totalSources) return false;
  if (run.status === "succeeded" && run.failedSources !== 0) return false;
  if ((run.status === "partial" || run.status === "failed") && run.failedSources === 0) {
    return false;
  }
  const sourceIds = new Set(run.sourceIds);
  const resultIds = run.results.map((result) => result.sourceId);
  if (
    new Set(resultIds).size !== resultIds.length ||
    resultIds.some((sourceId) => !sourceIds.has(sourceId))
  ) {
    return false;
  }
  if (
    run.status === "succeeded" &&
    (run.results.length !== run.totalSources ||
      run.results.some(
        (result) => result.state !== "ok" || result.servingCachedDataset
      ))
  ) {
    return false;
  }
  return true;
}

function isCount(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isParserResult(value: unknown): value is ParserRun["results"][number] {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<ParserRun["results"][number]>;
  const states = new Set([
    "ok",
    "partial",
    "fetch_error",
    "http_error",
    "blocked_by_protection",
    "proxy_required",
    "quality_error",
    "timed_out",
    "never_fetched",
    "error"
  ]);
  return (
    typeof result.sourceId === "string" &&
    /^[A-Za-z0-9_.:-]{1,120}$/.test(result.sourceId) &&
    typeof result.state === "string" &&
    states.has(result.state) &&
    typeof result.servingCachedDataset === "boolean"
  );
}

export async function enqueueParserRun(
  requestId: string,
  selection: ParserSelection,
  reason: string
): Promise<{ run: ParserRun; deduplicated: boolean }> {
  const payload = await parserRequest("admin/orchestrator/parser-runs", {
    method: "POST",
    body: JSON.stringify({
      requestId,
      sourceIds: selection.sourceIds ?? [],
      sectionIds: selection.sectionIds ?? [],
      reason,
      attemptPurpose: selection.attemptPurpose ?? "manual",
      originOccurrenceId: selection.originOccurrenceId ?? null,
      recoveryChainId: selection.recoveryChainId ?? null
    })
  });
  const envelope = payload as { run?: unknown; deduplicated?: unknown };
  if (!isParserRun(envelope.run) || typeof envelope.deduplicated !== "boolean") {
    throw new Error("Parser control API returned an invalid enqueue response");
  }
  return { run: envelope.run, deduplicated: envelope.deduplicated };
}

export async function getParserRun(runId: string): Promise<ParserRun> {
  if (!/^[a-f0-9]{32}$/.test(runId)) {
    throw new Error("Invalid local parser run ID");
  }
  const payload = await parserRequest(`admin/orchestrator/parser-runs/${runId}`);
  const envelope = payload as { run?: unknown };
  if (!isParserRun(envelope.run)) {
    throw new Error("Parser control API returned an invalid run response");
  }
  return envelope.run;
}
