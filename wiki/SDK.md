# TypeScript и C# SDK

Оба клиента используют канонический GraphQL endpoint:

```text
https://api.kolodahearthstone.com/v1/graphql
```

Встроено:

- `Authorization: Bearer` без токенов в URL;
- timeout 8 секунд;
- Apollo Persisted Queries;
- cursor pagination;
- типизированный поиск, карты и история патчей;
- generic GraphQL method для любых новых полей API.

## TypeScript

```ts
import { KolodaClient } from "@manacost-labs/koloda-hearthstone-api";

const api = new KolodaClient({ token: process.env.KOLODA_API_TOKEN });
const results = await api.search({ query: "Reno" });
```

## C#

```csharp
var api = new KolodaClient(new HttpClient(), new KolodaClientOptions {
    Token = Environment.GetEnvironmentVariable("KOLODA_API_TOKEN")
});
var results = await api.SearchAsync(new SearchRequest("Reno"));
```

Полный reference: [SDK](../docs/SDK.md).
