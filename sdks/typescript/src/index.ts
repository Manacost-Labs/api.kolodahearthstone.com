export const DEFAULT_GRAPHQL_URL =
  "https://api.kolodahearthstone.com/v1/graphql";

export interface PageInfo {
  limit: number;
  offset: number;
  total: number;
  hasNextPage: boolean;
  nextCursor: string | null;
}

export interface Connection<TItem> {
  items: TItem[];
  pageInfo: PageInfo;
}

export type SearchEntityKind =
  | "CARD"
  | "MINION"
  | "HERO"
  | "ARCHETYPE"
  | "SOURCE";

export interface SearchResult {
  kind: SearchEntityKind;
  entityId: string;
  name: string;
  nameRu: string | null;
  subtitle: string | null;
  imageUrl: string | null;
  sourceId: string;
  updatedAt: string | null;
  metadata: Record<string, unknown>;
  horizontalImageUrl: string | null;
}

export interface Card {
  collection: string;
  cardId: string;
  dbf: number | null;
  nameRu: string | null;
  nameEn: string | null;
  cardType: string | null;
  manaCost: number | null;
  attack: number | null;
  health: number | null;
  imageUrl: string | null;
  horizontalImageUrl: string | null;
  isActive: boolean | null;
  updatedAt: string | null;
}

export interface GameStatistic {
  snapshotId: number | null;
  sourceId: string;
  datasetVersion: string;
  domain: string;
  entityKey: string;
  entityType: string;
  name: string | null;
  nameRu: string | null;
  patch: string | null;
  formatName: string | null;
  rankRange: string | null;
  mode: string | null;
  games: number | null;
  winRate: number | null;
  popularity: number | null;
  pickRate: number | null;
  avgPlacement: number | null;
  score: number | null;
  metrics: Record<string, unknown> | null;
}

export interface PatchMetricDelta {
  metric: string;
  beforeValue: number | null;
  afterValue: number | null;
  absoluteChange: number | null;
  percentChange: number | null;
}

export interface StatisticPatchComparison {
  entityKey: string;
  fromPatch: string;
  toPatch: string;
  before: GameStatistic | null;
  after: GameStatistic | null;
  deltas: PatchMetricDelta[];
}

export interface GraphQLErrorShape {
  message: string;
  path?: Array<string | number>;
  extensions?: Record<string, unknown>;
}

interface GraphQLWireResponse<TData> {
  data?: TData;
  errors?: GraphQLErrorShape[];
}

export interface KolodaClientOptions {
  endpoint?: string;
  token?: string;
  timeoutMs?: number;
  persistedQueries?: boolean;
  fetch?: typeof fetch;
}

export interface RequestOptions {
  operationName?: string;
  signal?: AbortSignal;
  persistedQuery?: boolean;
}

export class KolodaHttpError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Koloda API returned HTTP ${status}`);
    this.name = "KolodaHttpError";
    this.status = status;
  }
}

export class KolodaGraphQLError extends Error {
  readonly errors: GraphQLErrorShape[];

  constructor(errors: GraphQLErrorShape[]) {
    super(errors[0]?.message ?? "Koloda GraphQL request failed");
    this.name = "KolodaGraphQLError";
    this.errors = errors;
  }
}

const SEARCH_QUERY = `query Search($query: String!, $kinds: [SearchEntityKind!], $after: String, $limit: Int!) {
  search(query: $query, kinds: $kinds, after: $after, limit: $limit) {
    items { kind entityId name nameRu subtitle imageUrl sourceId updatedAt metadata horizontalImageUrl }
    pageInfo { limit offset total hasNextPage nextCursor }
  }
}`;

const CARDS_QUERY = `query Cards($search: String, $collection: String, $after: String, $limit: Int!) {
  cards(search: $search, collection: $collection, after: $after, limit: $limit) {
    items { collection cardId dbf nameRu nameEn cardType manaCost attack health imageUrl horizontalImageUrl isActive updatedAt }
    pageInfo { limit offset total hasNextPage nextCursor }
  }
}`;

const STATISTIC_HISTORY_QUERY = `query StatisticHistory($entityKey: String!, $domain: String, $formatName: String, $rankRange: String, $after: String, $limit: Int!) {
  statisticHistory(entityKey: $entityKey, domain: $domain, formatName: $formatName, rankRange: $rankRange, after: $after, limit: $limit) {
    items { snapshotId sourceId datasetVersion domain entityKey entityType name nameRu patch formatName rankRange mode games winRate popularity pickRate avgPlacement score metrics }
    pageInfo { limit offset total hasNextPage nextCursor }
  }
}`;

const PATCH_COMPARISON_QUERY = `query CompareStatisticPatches($entityKey: String!, $fromPatch: String!, $toPatch: String!, $domain: String, $formatName: String, $rankRange: String) {
  compareStatisticPatches(entityKey: $entityKey, fromPatch: $fromPatch, toPatch: $toPatch, domain: $domain, formatName: $formatName, rankRange: $rankRange) {
    entityKey fromPatch toPatch
    before { snapshotId sourceId datasetVersion domain entityKey entityType name nameRu patch formatName rankRange mode games winRate popularity pickRate avgPlacement score metrics }
    after { snapshotId sourceId datasetVersion domain entityKey entityType name nameRu patch formatName rankRange mode games winRate popularity pickRate avgPlacement score metrics }
    deltas { metric beforeValue afterValue absoluteChange percentChange }
  }
}`;

function hasPersistedQueryMiss(errors: GraphQLErrorShape[] | undefined): boolean {
  return Boolean(
    errors?.some(
      (error) => error.extensions?.["code"] === "PERSISTED_QUERY_NOT_FOUND",
    ),
  );
}

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export class KolodaClient {
  readonly endpoint: string;
  private readonly token: string | undefined;
  private readonly timeoutMs: number;
  private readonly persistedQueries: boolean;
  private readonly fetchImplementation: typeof fetch;

  constructor(options: KolodaClientOptions = {}) {
    this.endpoint = options.endpoint ?? DEFAULT_GRAPHQL_URL;
    this.token = options.token;
    this.timeoutMs = options.timeoutMs ?? 8_000;
    this.persistedQueries = options.persistedQueries ?? true;
    this.fetchImplementation = options.fetch ?? globalThis.fetch;
    if (typeof this.fetchImplementation !== "function") {
      throw new TypeError("A Fetch API implementation is required");
    }
  }

  async request<TData, TVariables extends Record<string, unknown>>(
    document: string,
    variables: TVariables,
    options: RequestOptions = {},
  ): Promise<TData> {
    const usePersistedQuery =
      options.persistedQuery ?? this.persistedQueries;
    const hash = usePersistedQuery ? await sha256(document) : undefined;
    let response = await this.send<TData>(
      document,
      variables,
      options,
      hash,
      !usePersistedQuery,
    );
    if (usePersistedQuery && hasPersistedQueryMiss(response.errors)) {
      response = await this.send<TData>(
        document,
        variables,
        options,
        hash,
        true,
      );
    }
    if (response.errors?.length) {
      throw new KolodaGraphQLError(response.errors);
    }
    if (response.data === undefined) {
      throw new KolodaGraphQLError([{ message: "GraphQL response has no data" }]);
    }
    return response.data;
  }

  async search(input: {
    query: string;
    kinds?: SearchEntityKind[];
    after?: string;
    limit?: number;
  }): Promise<Connection<SearchResult>> {
    const data = await this.request<
      { search: Connection<SearchResult> },
      Record<string, unknown>
    >(
      SEARCH_QUERY,
      {
        query: input.query,
        kinds: input.kinds ?? null,
        after: input.after ?? null,
        limit: input.limit ?? 30,
      },
      { operationName: "Search" },
    );
    return data.search;
  }

  async cards(input: {
    search?: string;
    collection?: string;
    after?: string;
    limit?: number;
  } = {}): Promise<Connection<Card>> {
    const data = await this.request<
      { cards: Connection<Card> },
      Record<string, unknown>
    >(
      CARDS_QUERY,
      {
        search: input.search ?? null,
        collection: input.collection ?? null,
        after: input.after ?? null,
        limit: input.limit ?? 50,
      },
      { operationName: "Cards" },
    );
    return data.cards;
  }

  async statisticHistory(input: {
    entityKey: string;
    domain?: string;
    formatName?: string;
    rankRange?: string;
    after?: string;
    limit?: number;
  }): Promise<Connection<GameStatistic>> {
    const data = await this.request<
      { statisticHistory: Connection<GameStatistic> },
      Record<string, unknown>
    >(
      STATISTIC_HISTORY_QUERY,
      {
        entityKey: input.entityKey,
        domain: input.domain ?? null,
        formatName: input.formatName ?? null,
        rankRange: input.rankRange ?? null,
        after: input.after ?? null,
        limit: input.limit ?? 50,
      },
      { operationName: "StatisticHistory" },
    );
    return data.statisticHistory;
  }

  async compareStatisticPatches(input: {
    entityKey: string;
    fromPatch: string;
    toPatch: string;
    domain?: string;
    formatName?: string;
    rankRange?: string;
  }): Promise<StatisticPatchComparison> {
    const data = await this.request<
      { compareStatisticPatches: StatisticPatchComparison },
      Record<string, unknown>
    >(
      PATCH_COMPARISON_QUERY,
      {
        entityKey: input.entityKey,
        fromPatch: input.fromPatch,
        toPatch: input.toPatch,
        domain: input.domain ?? null,
        formatName: input.formatName ?? null,
        rankRange: input.rankRange ?? null,
      },
      { operationName: "CompareStatisticPatches" },
    );
    return data.compareStatisticPatches;
  }

  async *paginate<TItem>(
    fetchPage: (after?: string) => Promise<Connection<TItem>>,
  ): AsyncGenerator<TItem, void, undefined> {
    let after: string | undefined;
    do {
      const page = await fetchPage(after);
      for (const item of page.items) {
        yield item;
      }
      after = page.pageInfo.nextCursor ?? undefined;
    } while (after !== undefined);
  }

  private async send<TData>(
    document: string,
    variables: Record<string, unknown>,
    options: RequestOptions,
    hash: string | undefined,
    includeDocument: boolean,
  ): Promise<GraphQLWireResponse<TData>> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    const abort = () => controller.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    const body: Record<string, unknown> = { variables };
    if (options.operationName) body["operationName"] = options.operationName;
    if (includeDocument) body["query"] = document;
    if (hash) {
      body["extensions"] = {
        persistedQuery: { version: 1, sha256Hash: hash },
      };
    }
    const headers: Record<string, string> = {
      accept: "application/json",
      "content-type": "application/json",
    };
    if (this.token) headers["authorization"] = `Bearer ${this.token}`;
    try {
      const response = await this.fetchImplementation(this.endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) throw new KolodaHttpError(response.status);
      return (await response.json()) as GraphQLWireResponse<TData>;
    } finally {
      clearTimeout(timeout);
      options.signal?.removeEventListener("abort", abort);
    }
  }
}
