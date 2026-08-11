import { defineConfig } from "@trigger.dev/sdk";

const project = process.env.TRIGGER_PROJECT_REF;
if (!project) {
  throw new Error("TRIGGER_PROJECT_REF is required");
}

export default defineConfig({
  project,
  runtime: "node-22",
  dirs: ["./src/trigger"],
  maxDuration: 300,
  retries: {
    enabledInDev: false,
    default: {
      maxAttempts: 1,
      minTimeoutInMs: 2_000,
      maxTimeoutInMs: 15_000,
      factor: 2,
      randomize: true
    }
  }
});
