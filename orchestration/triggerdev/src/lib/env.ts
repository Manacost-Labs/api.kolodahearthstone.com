function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

export function parserControlConfig(): {
  baseUrl: URL;
  token: string;
  runTimeoutMs: number;
} {
  const baseUrl = new URL(required("PARSER_CONTROL_BASE_URL"));
  if (baseUrl.protocol !== "https:") {
    throw new Error("PARSER_CONTROL_BASE_URL must use HTTPS");
  }
  if (baseUrl.username || baseUrl.password || baseUrl.search || baseUrl.hash) {
    throw new Error("PARSER_CONTROL_BASE_URL must not contain credentials or query data");
  }
  baseUrl.pathname = baseUrl.pathname.replace(/\/+$/, "");

  const timeoutSeconds = Number(process.env.PARSER_RUN_TIMEOUT_SECONDS ?? "4500");
  if (!Number.isFinite(timeoutSeconds) || timeoutSeconds < 60 || timeoutSeconds > 7200) {
    throw new Error("PARSER_RUN_TIMEOUT_SECONDS must be between 60 and 7200");
  }

  return {
    baseUrl,
    token: required("PARSER_ORCHESTRATOR_TOKEN"),
    runTimeoutMs: timeoutSeconds * 1_000
  };
}
