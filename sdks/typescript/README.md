# Koloda Hearthstone API — TypeScript SDK

```bash
npm install @manacost-labs/koloda-hearthstone-api
```

```ts
import { KolodaClient } from "@manacost-labs/koloda-hearthstone-api";

const api = new KolodaClient({ token: process.env.KOLODA_API_TOKEN });
const results = await api.search({ query: "Reno", kinds: ["CARD", "ARCHETYPE"] });
```

The client uses `https://api.kolodahearthstone.com/v1/graphql`, Bearer tokens,
8-second timeouts and Apollo Persisted Queries by default. `request()` supports
any GraphQL operation; `search()`, `cards()`, `statisticHistory()` and
`compareStatisticPatches()` provide typed common operations.
