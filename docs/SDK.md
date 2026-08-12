# Official SDKs

Both SDKs target the canonical GraphQL endpoint:

```text
https://api.kolodahearthstone.com/v1/graphql
```

They send API credentials only through `Authorization: Bearer`, apply an
8-second client deadline, perform the Apollo Persisted Query handshake and
support cursor iteration. Both also expose a generic GraphQL request method, so
new query fields can be used before a convenience wrapper is released.

## TypeScript

Source package: [`sdks/typescript/`](../sdks/typescript/README.md)

```ts
import { KolodaClient } from "@manacost-labs/koloda-hearthstone-api";

const api = new KolodaClient({ token: process.env.KOLODA_API_TOKEN });
const firstPage = await api.search({
  query: "Reno",
  kinds: ["CARD", "ARCHETYPE"],
});
```

Build and test locally with `npm ci`, `npm run check` and `npm test` from
`sdks/typescript`.

## C# / .NET

Source package: [`sdks/dotnet/KolodaHearthstone.Api/`](../sdks/dotnet/KolodaHearthstone.Api/README.md)

```csharp
using KolodaHearthstone.Api;

var api = new KolodaClient(new HttpClient(), new KolodaClientOptions {
    Token = Environment.GetEnvironmentVariable("KOLODA_API_TOKEN")
});
var firstPage = await api.SearchAsync(new SearchRequest(
    "Reno",
    [SearchEntityKind.CARD, SearchEntityKind.ARCHETYPE]
));
```

The package targets .NET 8 and has no third-party runtime dependencies. CI
builds the package and runs the persisted-query smoke application on every PR.
