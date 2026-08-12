<?php
declare(strict_types=1);

/**
 * Pure dataset-to-statistics normalization used by the importer and contract
 * tests. It intentionally has no database or network dependencies.
 */

function statistics_number($value): ?float
{
    if ($value === null || $value === '' || is_bool($value)) {
        return null;
    }
    if (is_string($value)) {
        $value = trim(str_replace(['%', ' '], ['', ''], $value));
        if ($value === '') {
            return null;
        }
    }
    return is_numeric($value) ? (float)$value : null;
}

function statistics_integer($value): ?int
{
    $number = statistics_number($value);
    return $number === null ? null : (int)round($number);
}

function statistics_text($value): ?string
{
    if (!is_scalar($value)) {
        return null;
    }
    $text = trim((string)$value);
    return $text === '' ? null : $text;
}

function statistics_entity_key(array $row, string $fallbackPrefix, int $index): string
{
    foreach (['card_id', 'id', 'archetype', 'hero', 'name'] as $key) {
        $value = statistics_text($row[$key] ?? null);
        if ($value !== null) {
            return $value;
        }
    }
    $dbf = statistics_integer($row['dbfId'] ?? $row['dbf_id'] ?? null);
    return $dbf !== null ? 'dbf:' . $dbf : $fallbackPrefix . ':' . $index;
}

function statistics_row(array $row, string $entityType, int $index): array
{
    $cardId = statistics_text($row['card_id'] ?? $row['id'] ?? $row['hero_card_id'] ?? null);
    $name = statistics_text($row['archetype'] ?? $row['hero'] ?? $row['name_en'] ?? $row['name'] ?? null);
    $nameRu = statistics_text($row['name_ru'] ?? (($row['archetype'] ?? $row['hero'] ?? null) ? null : ($row['name'] ?? null)));
    $metrics = $row;
    unset($metrics['decks']);

    return [
        'entity_key' => statistics_entity_key($row, $entityType, $index),
        'entity_type' => $entityType,
        'card_id' => $cardId,
        'dbf_id' => statistics_integer($row['dbfId'] ?? $row['dbf_id'] ?? null),
        'name' => $name,
        'name_ru' => $nameRu,
        'class_name' => statistics_text($row['class_name'] ?? $row['class'] ?? $row['cardClass'] ?? null),
        'tier' => statistics_text($row['tier'] ?? null),
        'games' => statistics_integer($row['games'] ?? $row['total_games'] ?? $row['times_played'] ?? $row['data_points'] ?? null),
        'win_rate' => statistics_number($row['win_rate'] ?? $row['winrate'] ?? $row['deck_winrate'] ?? null),
        'popularity' => statistics_number($row['popularity_pct'] ?? $row['popularity'] ?? $row['deck_popularity'] ?? null),
        'pick_rate' => statistics_number($row['pick_rate_value'] ?? $row['pick_rate'] ?? null),
        'avg_placement' => statistics_number($row['avg_placement'] ?? $row['average_position'] ?? null),
        'score' => statistics_number($row['score'] ?? $row['climbing_speed'] ?? $row['climbing_speed_stars_per_hour'] ?? null),
        'image_url' => statistics_text($row['image_url'] ?? null),
        'source_url' => statistics_text($row['archetype_url'] ?? $row['url'] ?? null),
        'metrics' => $metrics,
    ];
}

function statistics_snapshot(array $base, array $rows): array
{
    $normalizedRows = [];
    foreach ($rows as $index => $row) {
        if (!is_array($row)) {
            continue;
        }
        $normalizedRows[] = statistics_row($row, (string)$base['entity_type'], (int)$index);
    }
    $base['rows'] = $normalizedRows;
    return $base;
}

function statistics_base_snapshot(
    string $sourceId,
    string $datasetVersion,
    string $domain,
    string $entityType,
    string $fetchedAt,
    array $overrides = []
): array {
    return array_merge([
        'source_id' => $sourceId,
        'dataset_version' => $datasetVersion,
        'domain' => $domain,
        'entity_type' => $entityType,
        'format_name' => 'all',
        'rank_range' => 'all',
        'period' => 'current',
        'mode' => 'default',
        'rating_bracket' => 'all',
        'patch' => null,
        'source_url' => null,
        'fetched_at' => $fetchedAt,
        'metadata' => [],
    ], $overrides);
}

function normalize_hsguru_statistics(string $sourceId, string $datasetVersion, array $payload): array
{
    $structured = is_array($payload['data']['structured'] ?? null) ? $payload['data']['structured'] : [];
    $fetchedAt = statistics_text($payload['fetched_at'] ?? $structured['fetched_at'] ?? null) ?? gmdate(DATE_ATOM);
    $snapshots = [];

    foreach (($structured['slices'] ?? []) as $slice) {
        if (!is_array($slice) || !is_array($slice['rows'] ?? null)) {
            continue;
        }
        $period = statistics_text($slice['period'] ?? null) ?? 'current';
        $snapshots[] = statistics_snapshot(statistics_base_snapshot(
            $sourceId,
            $datasetVersion,
            'constructed_meta',
            'archetype',
            statistics_text($slice['fetched_at'] ?? null) ?? $fetchedAt,
            [
                'format_name' => statistics_text($slice['format'] ?? null) ?? 'all',
                'rank_range' => statistics_text($slice['rank'] ?? null) ?? 'all',
                'period' => $period,
                'mode' => statistics_text($slice['coin'] ?? null) ?? 'any_player',
                'patch' => strpos($period, 'patch_') === 0 ? substr($period, 6) : null,
                'source_url' => statistics_text($slice['source_url'] ?? null),
                'metadata' => ['row_counts' => $slice['row_counts'] ?? null],
            ]
        ), $slice['rows']);
    }

    $catalog = is_array($structured['current_catalog'] ?? null) ? $structured['current_catalog'] : [];
    $catalogRows = is_array($catalog['archetypes'] ?? null) ? $catalog['archetypes'] : [];
    $byFormat = [];
    foreach ($catalogRows as $row) {
        if (!is_array($row)) {
            continue;
        }
        $format = statistics_text($row['format'] ?? null) ?? 'all';
        $byFormat[$format][] = $row;
    }
    foreach ($byFormat as $format => $rows) {
        $criteria = is_array($catalog['criteria'] ?? null) ? $catalog['criteria'] : [];
        $period = statistics_text($criteria['period'] ?? null) ?? 'current';
        $snapshots[] = statistics_snapshot(statistics_base_snapshot(
            $sourceId,
            $datasetVersion,
            'constructed_archetypes',
            'archetype',
            $fetchedAt,
            [
                'format_name' => $format,
                'rank_range' => statistics_text($criteria['rank'] ?? null) ?? 'all',
                'period' => $period,
                'patch' => strpos($period, 'patch_') === 0 ? substr($period, 6) : null,
                'source_url' => $rows[0]['source_url'] ?? null,
                'metadata' => ['coverage' => $catalog['coverage'][$format] ?? null],
            ]
        ), $rows);
    }

    return $snapshots;
}

function normalize_card_statistics(string $sourceId, string $datasetVersion, array $payload): array
{
    $structured = is_array($payload['data']['structured'] ?? null) ? $payload['data']['structured'] : [];
    $cards = is_array($structured['cards'] ?? null) ? $structured['cards'] : [];
    if ($cards === []) {
        return [];
    }
    $fetchedAt = statistics_text($payload['fetched_at'] ?? null) ?? gmdate(DATE_ATOM);
    $type = statistics_text($structured['type'] ?? null) ?? '';

    if ($type === 'arena_card_tiers') {
        $mode = strpos($sourceId, 'underground') !== false ? 'underground' : 'regular';
        return [statistics_snapshot(statistics_base_snapshot(
            $sourceId,
            $datasetVersion,
            'arena_cards',
            'card',
            $fetchedAt,
            [
                'format_name' => 'arena',
                'period' => strpos($sourceId, 'firestone') === 0 ? 'past_3_days' : 'current',
                'mode' => $mode,
                'source_url' => statistics_text($structured['source']['url'] ?? $payload['final_url'] ?? null),
                'metadata' => [
                    'total_data_points' => $structured['total_data_points'] ?? null,
                    'by_class' => $structured['by_class'] ?? null,
                ],
            ]
        ), $cards)];
    }

    if ($type === 'card_stats') {
        $gameType = statistics_text($structured['game_type'] ?? null) ?? 'RANKED_STANDARD';
        $format = strpos($gameType, 'WILD') !== false ? 'wild' : 'standard';
        return [statistics_snapshot(statistics_base_snapshot(
            $sourceId,
            $datasetVersion,
            'constructed_cards',
            'card',
            $fetchedAt,
            [
                'format_name' => $format,
                'rank_range' => statistics_text($structured['rank_range'] ?? null) ?? 'all',
                'period' => statistics_text($structured['time_range'] ?? null) ?? 'current',
                'source_url' => statistics_text($structured['source']['url'] ?? $payload['final_url'] ?? null),
                'metadata' => ['sort_mode' => $structured['sort_mode'] ?? null],
            ]
        ), $cards)];
    }

    return [];
}

function normalize_hero_statistics(string $sourceId, string $datasetVersion, array $payload): array
{
    $structured = is_array($payload['data']['structured'] ?? null) ? $payload['data']['structured'] : [];
    $heroes = is_array($structured['heroes'] ?? null) ? $structured['heroes'] : [];
    if ($heroes === []) {
        return [];
    }

    $filters = is_array($structured['filters'] ?? null) ? $structured['filters'] : [];
    $rating = statistics_text($filters['mmr_percentile'] ?? null);
    if ($rating === null) {
        $mmr = statistics_text($structured['mmr'] ?? null);
        if ($mmr !== null && preg_match('/mmr-(\d+)/', $mmr, $match)) {
            $rating = 'TOP_' . $match[1] . '_PERCENT';
        }
    }
    $rating = $rating ?? 'all';
    $period = statistics_text($filters['time_range'] ?? $structured['time_period'] ?? null) ?? 'current';

    return [statistics_snapshot(statistics_base_snapshot(
        $sourceId,
        $datasetVersion,
        'bg_heroes',
        'hero',
        statistics_text($payload['fetched_at'] ?? $structured['fetched_at'] ?? null) ?? gmdate(DATE_ATOM),
        [
            'format_name' => 'battlegrounds',
            'period' => $period,
            'mode' => statistics_text($structured['mode'] ?? null) ?? 'solo',
            'rating_bracket' => $rating,
            'source_url' => statistics_text($structured['source']['url'] ?? $payload['final_url'] ?? null),
        ]
    ), $heroes)];
}

function normalize_statistics_dataset(string $sourceId, string $datasetVersion, array $payload): array
{
    if ($sourceId === 'hsguru_meta_matrix') {
        return normalize_hsguru_statistics($sourceId, $datasetVersion, $payload);
    }
    if ($sourceId === 'hsreplay_battlegrounds_heroes'
        || $sourceId === 'hsreplay_battlegrounds_hero_details'
        || strpos($sourceId, 'firestone_bg_heroes_') === 0
    ) {
        return normalize_hero_statistics($sourceId, $datasetVersion, $payload);
    }
    if ($sourceId === 'hsreplay_arena_cards_advanced'
        || strpos($sourceId, 'firestone_arena_cards_') === 0
        || preg_match('/^hsreplay_cards_(?:wild_)?(?:platinum|diamond|diamond_4_1|legend)_/', $sourceId)
    ) {
        return normalize_card_statistics($sourceId, $datasetVersion, $payload);
    }
    return [];
}
