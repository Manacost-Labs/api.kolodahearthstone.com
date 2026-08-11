import { schedules } from "@trigger.dev/sdk";

import { runLocalParserJob } from "./parser-job.js";

// Deliberately has no declarative cron. Attach a schedule only after the
// corresponding systemd timer is disabled, so two control planes never race.
export const viciousSyndicateCanary = schedules.task({
  id: "hearthstone-vicious-syndicate-canary",
  queue: { concurrencyLimit: 1 },
  retry: { maxAttempts: 2 },
  ttl: "30m",
  run: async (_payload, { ctx }) =>
    runLocalParserJob(
      {
        sourceIds: ["vicious_syndicate_live_beta", "vicious_syndicate_radars"],
        reason: "Trigger.dev canary: Vicious Syndicate"
      },
      ctx.run.id,
      ctx.task.id
    )
});
