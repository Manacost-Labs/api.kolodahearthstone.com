using System.Text.Json;
using System.Text.Json.Serialization;

namespace KolodaHearthstone.Api;

public sealed record PageInfo(
    int Limit,
    int Offset,
    long Total,
    bool HasNextPage,
    string? NextCursor
);

public sealed record Connection<T>(IReadOnlyList<T> Items, PageInfo PageInfo);

[JsonConverter(typeof(JsonStringEnumConverter))]
public enum SearchEntityKind
{
    CARD,
    MINION,
    HERO,
    ARCHETYPE,
    SOURCE,
}

public sealed record SearchResult(
    SearchEntityKind Kind,
    string EntityId,
    string Name,
    string? NameRu,
    string? Subtitle,
    string? ImageUrl,
    string SourceId,
    DateTimeOffset? UpdatedAt,
    JsonElement Metadata
);

public sealed record Card(
    string Collection,
    string CardId,
    long? Dbf,
    string? NameRu,
    string? NameEn,
    string? CardType,
    int? ManaCost,
    int? Attack,
    int? Health,
    string? ImageUrl,
    bool? IsActive,
    DateTimeOffset? UpdatedAt
);

public sealed record GameStatistic
{
    public long? SnapshotId { get; init; }
    public required string SourceId { get; init; }
    public required string DatasetVersion { get; init; }
    public required string Domain { get; init; }
    public required string EntityKey { get; init; }
    public required string EntityType { get; init; }
    public string? Name { get; init; }
    public string? NameRu { get; init; }
    public string? Patch { get; init; }
    public string? FormatName { get; init; }
    public string? RankRange { get; init; }
    public string? Mode { get; init; }
    public long? Games { get; init; }
    public double? WinRate { get; init; }
    public double? Popularity { get; init; }
    public double? PickRate { get; init; }
    public double? AvgPlacement { get; init; }
    public double? Score { get; init; }
    public JsonElement? Metrics { get; init; }
}

public sealed record PatchMetricDelta(
    string Metric,
    double? BeforeValue,
    double? AfterValue,
    double? AbsoluteChange,
    double? PercentChange
);

public sealed record StatisticPatchComparison(
    string EntityKey,
    string FromPatch,
    string ToPatch,
    GameStatistic? Before,
    GameStatistic? After,
    IReadOnlyList<PatchMetricDelta> Deltas
);

public sealed record SearchRequest(
    string Query,
    IReadOnlyList<SearchEntityKind>? Kinds = null,
    string? After = null,
    int Limit = 30
);

public sealed record CardsRequest(
    string? Search = null,
    string? Collection = null,
    string? After = null,
    int Limit = 50
);

public sealed record StatisticHistoryRequest(
    string EntityKey,
    string? Domain = null,
    string? FormatName = null,
    string? RankRange = null,
    string? After = null,
    int Limit = 50
);

public sealed record StatisticPatchComparisonRequest(
    string EntityKey,
    string FromPatch,
    string ToPatch,
    string? Domain = null,
    string? FormatName = null,
    string? RankRange = null
);
