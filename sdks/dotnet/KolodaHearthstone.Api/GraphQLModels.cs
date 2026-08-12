using System.Text.Json;

namespace KolodaHearthstone.Api;

public sealed record KolodaGraphQLError
{
    public required string Message { get; init; }
    public IReadOnlyList<JsonElement>? Path { get; init; }
    public IReadOnlyDictionary<string, JsonElement>? Extensions { get; init; }

    public bool HasCode(string code) =>
        Extensions is not null
        && Extensions.TryGetValue("code", out JsonElement value)
        && value.ValueKind == JsonValueKind.String
        && string.Equals(value.GetString(), code, StringComparison.Ordinal);
}

internal sealed record GraphQLResponse<T>
{
    public T? Data { get; init; }
    public IReadOnlyList<KolodaGraphQLError>? Errors { get; init; }
}

public sealed class KolodaHttpException(int statusCode)
    : HttpRequestException($"Koloda API returned HTTP {statusCode}")
{
    public int StatusCodeValue { get; } = statusCode;
}

public sealed class KolodaGraphQLException(IReadOnlyList<KolodaGraphQLError> errors)
    : Exception(errors.FirstOrDefault()?.Message ?? "Koloda GraphQL request failed")
{
    public IReadOnlyList<KolodaGraphQLError> Errors { get; } = errors;
}
