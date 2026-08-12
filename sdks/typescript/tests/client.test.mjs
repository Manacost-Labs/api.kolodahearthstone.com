import assert from "node:assert/strict";
import { test } from "node:test";

import { KolodaClient } from "../dist/index.js";

test("registers an Apollo persisted query and sends the token safely", async () => {
  const requests = [];
  const fetch = async (_url, init) => {
    requests.push(init);
    if (requests.length === 1) {
      return Response.json({
        errors: [
          {
            message: "Persisted query was not found",
            extensions: { code: "PERSISTED_QUERY_NOT_FOUND" },
          },
        ],
      });
    }
    return Response.json({ data: { health: { status: "ok" } } });
  };
  const client = new KolodaClient({ token: "test-token", fetch });

  const data = await client.request(
    "query Health { health { status } }",
    {},
    { operationName: "Health" },
  );

  assert.equal(data.health.status, "ok");
  assert.equal(requests.length, 2);
  assert.equal(requests[0].headers.authorization, "Bearer test-token");
  assert.equal(JSON.parse(requests[0].body).query, undefined);
  assert.match(
    JSON.parse(requests[0].body).extensions.persistedQuery.sha256Hash,
    /^[a-f0-9]{64}$/,
  );
  assert.match(JSON.parse(requests[1].body).query, /^query Health/);
});

test("iterates cursor connections without offset pagination", async () => {
  const client = new KolodaClient({ persistedQueries: false, fetch: globalThis.fetch });
  const seen = [];
  const items = [];

  for await (const item of client.paginate(async (after) => {
    seen.push(after);
    return after === undefined
      ? {
          items: [1, 2],
          pageInfo: { nextCursor: "next", hasNextPage: true },
        }
      : {
          items: [3],
          pageInfo: { nextCursor: null, hasNextPage: false },
        };
  })) {
    items.push(item);
  }

  assert.deepEqual(seen, [undefined, "next"]);
  assert.deepEqual(items, [1, 2, 3]);
});
