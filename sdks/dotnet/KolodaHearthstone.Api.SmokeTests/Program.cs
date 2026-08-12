using System.Net;
using System.Text;
using System.Text.Json;
using KolodaHearthstone.Api;

var handler = new PersistedQueryHandler();
using var http = new HttpClient(handler);
var client = new KolodaClient(
    http,
    new KolodaClientOptions { Token = "test-token" }
);
HealthData data = await client.ExecuteAsync<HealthData, object>(
    "query Health { health { status } }",
    new { },
    "Health"
);
Assert(data.Health.Status == "ok", "health response must deserialize");
Assert(handler.Bodies.Count == 2, "persisted query miss must register once");
using JsonDocument first = JsonDocument.Parse(handler.Bodies[0]);
using JsonDocument second = JsonDocument.Parse(handler.Bodies[1]);
Assert(!first.RootElement.TryGetProperty("query", out _), "first request must be hash-only");
Assert(second.RootElement.TryGetProperty("query", out _), "fallback must include query");
Assert(handler.Authorization == "Bearer test-token", "Bearer token must be sent");
Console.WriteLine("C# SDK smoke tests: ok");

static void Assert(bool value, string message)
{
    if (!value) throw new InvalidOperationException(message);
}

internal sealed record HealthData(Health Health);
internal sealed record Health(string Status);

internal sealed class PersistedQueryHandler : HttpMessageHandler
{
    public List<string> Bodies { get; } = [];
    public string? Authorization { get; private set; }

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken
    )
    {
        Authorization = request.Headers.Authorization?.ToString();
        Bodies.Add(await request.Content!.ReadAsStringAsync(cancellationToken));
        string json = Bodies.Count == 1
            ? """{"errors":[{"message":"missing","extensions":{"code":"PERSISTED_QUERY_NOT_FOUND"}}]}"""
            : """{"data":{"health":{"status":"ok"}}}""";
        return new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json"),
        };
    }
}
