namespace KolodaHearthstone.Api;

public static class KolodaQueries
{
    public const string Search = """
        query Search($query: String!, $kinds: [SearchEntityKind!], $after: String, $limit: Int!) {
          search(query: $query, kinds: $kinds, after: $after, limit: $limit) {
            items { kind entityId name nameRu subtitle imageUrl sourceId updatedAt metadata }
            pageInfo { limit offset total hasNextPage nextCursor }
          }
        }
        """;

    public const string Cards = """
        query Cards($search: String, $collection: String, $after: String, $limit: Int!) {
          cards(search: $search, collection: $collection, after: $after, limit: $limit) {
            items { collection cardId dbf nameRu nameEn cardType manaCost attack health imageUrl isActive updatedAt }
            pageInfo { limit offset total hasNextPage nextCursor }
          }
        }
        """;

    public const string StatisticHistory = """
        query StatisticHistory($entityKey: String!, $domain: String, $formatName: String, $rankRange: String, $after: String, $limit: Int!) {
          statisticHistory(entityKey: $entityKey, domain: $domain, formatName: $formatName, rankRange: $rankRange, after: $after, limit: $limit) {
            items { snapshotId sourceId datasetVersion domain entityKey entityType name nameRu patch formatName rankRange mode games winRate popularity pickRate avgPlacement score metrics }
            pageInfo { limit offset total hasNextPage nextCursor }
          }
        }
        """;

    public const string CompareStatisticPatches = """
        query CompareStatisticPatches($entityKey: String!, $fromPatch: String!, $toPatch: String!, $domain: String, $formatName: String, $rankRange: String) {
          compareStatisticPatches(entityKey: $entityKey, fromPatch: $fromPatch, toPatch: $toPatch, domain: $domain, formatName: $formatName, rankRange: $rankRange) {
            entityKey fromPatch toPatch
            before { snapshotId sourceId datasetVersion domain entityKey entityType name nameRu patch formatName rankRange mode games winRate popularity pickRate avgPlacement score metrics }
            after { snapshotId sourceId datasetVersion domain entityKey entityType name nameRu patch formatName rankRange mode games winRate popularity pickRate avgPlacement score metrics }
            deltas { metric beforeValue afterValue absoluteChange percentChange }
          }
        }
        """;
}
