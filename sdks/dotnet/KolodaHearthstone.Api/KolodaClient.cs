using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace KolodaHearthstone.Api;

public sealed record KolodaClientOptions
{
    public Uri Endpoint { get; init; } = new(
        "https://api.kolodahearthstone.com/v1/graphql"
    );
    public string? Token { get; init; }
    public TimeSpan Timeout { get; init; } = TimeSpan.FromSeconds(8);
    public bool UsePersistedQueries { get; init; } = true;
}

public sealed class KolodaClient
{
    private static readonly JsonSerializerOptions JsonOptions = CreateJsonOptions();
    private readonly HttpClient httpClient;
    private readonly KolodaClientOptions options;

    public KolodaClient(HttpClient httpClient, KolodaClientOptions? options = null)
    {
        this.httpClient = httpClient ?? throw new ArgumentNullException(nameof(httpClient));
        this.options = options ?? new KolodaClientOptions();
        if (this.options.Timeout <= TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(nameof(options), "Timeout must be positive");
        }
    }

    public async Task<TData> ExecuteAsync<TData, TVariables>(
        string document,
        TVariables variables,
        string? operationName = null,
        CancellationToken cancellationToken = default
    )
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(document);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(options.Timeout);
        string? hash = options.UsePersistedQueries ? Sha256(document) : null;
        GraphQLResponse<TData> response = await SendAsync<TData, TVariables>(
            document,
            variables,
            operationName,
            hash,
            includeDocument: !options.UsePersistedQueries,
            timeout.Token
        ).ConfigureAwait(false);
        if (
            hash is not null
            && response.Errors?.Any(error => error.HasCode("PERSISTED_QUERY_NOT_FOUND")) == true
        )
        {
            response = await SendAsync<TData, TVariables>(
                document,
                variables,
                operationName,
                hash,
                includeDocument: true,
                timeout.Token
            ).ConfigureAwait(false);
        }
        if (response.Errors is { Count: > 0 })
        {
            throw new KolodaGraphQLException(response.Errors);
        }
        if (response.Data is null)
        {
            throw new KolodaGraphQLException(
                [new KolodaGraphQLError { Message = "GraphQL response has no data" }]
            );
        }
        return response.Data;
    }

    public async Task<Connection<SearchResult>> SearchAsync(
        SearchRequest request,
        CancellationToken cancellationToken = default
    )
    {
        ArgumentNullException.ThrowIfNull(request);
        var variables = new
        {
            query = request.Query,
            kinds = request.Kinds?.Select(kind => kind.ToString()).ToArray(),
            after = request.After,
            limit = request.Limit,
        };
        SearchEnvelope data = await ExecuteAsync<SearchEnvelope, object>(
            KolodaQueries.Search,
            variables,
            "Search",
            cancellationToken
        ).ConfigureAwait(false);
        return data.Search;
    }

    public async Task<Connection<Card>> CardsAsync(
        CardsRequest? request = null,
        CancellationToken cancellationToken = default
    )
    {
        request ??= new CardsRequest();
        var variables = new
        {
            search = request.Search,
            collection = request.Collection,
            after = request.After,
            limit = request.Limit,
        };
        CardsEnvelope data = await ExecuteAsync<CardsEnvelope, object>(
            KolodaQueries.Cards,
            variables,
            "Cards",
            cancellationToken
        ).ConfigureAwait(false);
        return data.Cards;
    }

    public async Task<Connection<GameStatistic>> StatisticHistoryAsync(
        StatisticHistoryRequest request,
        CancellationToken cancellationToken = default
    )
    {
        ArgumentNullException.ThrowIfNull(request);
        var variables = new
        {
            entityKey = request.EntityKey,
            domain = request.Domain,
            formatName = request.FormatName,
            rankRange = request.RankRange,
            after = request.After,
            limit = request.Limit,
        };
        StatisticHistoryEnvelope data = await ExecuteAsync<
            StatisticHistoryEnvelope,
            object
        >(
            KolodaQueries.StatisticHistory,
            variables,
            "StatisticHistory",
            cancellationToken
        ).ConfigureAwait(false);
        return data.StatisticHistory;
    }

    public async Task<StatisticPatchComparison> CompareStatisticPatchesAsync(
        StatisticPatchComparisonRequest request,
        CancellationToken cancellationToken = default
    )
    {
        ArgumentNullException.ThrowIfNull(request);
        var variables = new
        {
            entityKey = request.EntityKey,
            fromPatch = request.FromPatch,
            toPatch = request.ToPatch,
            domain = request.Domain,
            formatName = request.FormatName,
            rankRange = request.RankRange,
        };
        PatchComparisonEnvelope data = await ExecuteAsync<
            PatchComparisonEnvelope,
            object
        >(
            KolodaQueries.CompareStatisticPatches,
            variables,
            "CompareStatisticPatches",
            cancellationToken
        ).ConfigureAwait(false);
        return data.CompareStatisticPatches;
    }

    public async IAsyncEnumerable<T> PaginateAsync<T>(
        Func<string?, CancellationToken, Task<Connection<T>>> fetchPage,
        [System.Runtime.CompilerServices.EnumeratorCancellation]
            CancellationToken cancellationToken = default
    )
    {
        ArgumentNullException.ThrowIfNull(fetchPage);
        string? after = null;
        do
        {
            Connection<T> page = await fetchPage(after, cancellationToken)
                .ConfigureAwait(false);
            foreach (T item in page.Items)
            {
                yield return item;
            }
            after = page.PageInfo.NextCursor;
        } while (after is not null);
    }

    private async Task<GraphQLResponse<TData>> SendAsync<TData, TVariables>(
        string document,
        TVariables variables,
        string? operationName,
        string? hash,
        bool includeDocument,
        CancellationToken cancellationToken
    )
    {
        var payload = new Dictionary<string, object?>
        {
            ["variables"] = variables,
        };
        if (operationName is not null) payload["operationName"] = operationName;
        if (includeDocument) payload["query"] = document;
        if (hash is not null)
        {
            payload["extensions"] = new
            {
                persistedQuery = new { version = 1, sha256Hash = hash },
            };
        }
        using var message = new HttpRequestMessage(HttpMethod.Post, options.Endpoint)
        {
            Content = JsonContent.Create(payload, options: JsonOptions),
        };
        message.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (!string.IsNullOrWhiteSpace(options.Token))
        {
            message.Headers.Authorization = new AuthenticationHeaderValue(
                "Bearer",
                options.Token.Trim()
            );
        }
        using HttpResponseMessage response = await httpClient.SendAsync(
            message,
            HttpCompletionOption.ResponseHeadersRead,
            cancellationToken
        ).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new KolodaHttpException((int)response.StatusCode);
        }
        await using Stream stream = await response.Content.ReadAsStreamAsync(cancellationToken)
            .ConfigureAwait(false);
        GraphQLResponse<TData>? body = await JsonSerializer.DeserializeAsync<
            GraphQLResponse<TData>
        >(stream, JsonOptions, cancellationToken).ConfigureAwait(false);
        return body
            ?? throw new KolodaGraphQLException(
                [new KolodaGraphQLError { Message = "GraphQL response is empty" }]
            );
    }

    private static string Sha256(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    private static JsonSerializerOptions CreateJsonOptions()
    {
        var result = new JsonSerializerOptions(JsonSerializerDefaults.Web)
        {
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        };
        result.Converters.Add(new JsonStringEnumConverter());
        return result;
    }

    private sealed record SearchEnvelope(Connection<SearchResult> Search);
    private sealed record CardsEnvelope(Connection<Card> Cards);
    private sealed record StatisticHistoryEnvelope(
        Connection<GameStatistic> StatisticHistory
    );
    private sealed record PatchComparisonEnvelope(
        StatisticPatchComparison CompareStatisticPatches
    );
}
