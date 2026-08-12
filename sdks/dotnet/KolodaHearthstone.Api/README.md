# Koloda Hearthstone API — C# SDK

```csharp
using KolodaHearthstone.Api;

var http = new HttpClient();
var api = new KolodaClient(http, new KolodaClientOptions {
    Token = Environment.GetEnvironmentVariable("KOLODA_API_TOKEN")
});
var results = await api.SearchAsync(new SearchRequest(
    "Reno",
    [SearchEntityKind.CARD, SearchEntityKind.ARCHETYPE]
));
```

The client targets .NET 8, has no third-party runtime dependencies and uses
Bearer authentication, an 8-second timeout and Apollo Persisted Queries by
default. `ExecuteAsync` supports any GraphQL operation.
