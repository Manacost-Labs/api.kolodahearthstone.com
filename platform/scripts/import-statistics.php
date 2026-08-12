#!/usr/bin/env php
<?php
declare(strict_types=1);

require __DIR__ . '/statistics-normalizer.php';

const STATISTICS_PLATFORM_DIR = __DIR__ . '/..';
const STATISTICS_DATASET_DIR = '/srv/hs-data-api/data/datasets';
const STATISTICS_CARDS_INDEX = '/srv/hs-data-api/data/hearthstonejson.cards.ruRU.json';

function statistics_log(string $level, string $event, string $runId, array $context = []): void
{
    $record = array_merge([
        'timestamp' => gmdate('c'),
        'level' => $level,
        'event' => $event,
        'run_id' => $runId,
    ], $context);
    fwrite(
        $level === 'error' ? STDERR : STDOUT,
        json_encode($record, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR) . PHP_EOL
    );
}

function statistics_open_target(): array
{
    $composeDir = STATISTICS_PLATFORM_DIR . '/postgres';
    $process = proc_open(
        [
            '/usr/bin/sudo', '-n', 'docker', 'compose',
            '--project-directory', $composeDir,
            '-f', $composeDir . '/docker-compose.yml',
            'exec', '-T', 'postgres', 'sh', '-eu', '-c',
            'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data',
        ],
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
        $pipes,
        null,
        null,
        ['bypass_shell' => true]
    );
    if (!is_resource($process)) {
        throw new RuntimeException('Unable to start PostgreSQL statistics import process');
    }
    return [$process, $pipes];
}

function statistics_write_csv($stream, array $row): void
{
    if (fputcsv($stream, $row, ',', '"', '') === false) {
        throw new RuntimeException('Unable to write statistics CSV row');
    }
}

function statistics_dataset_files(): array
{
    $paths = glob(STATISTICS_DATASET_DIR . '/*.json') ?: [];
    $selected = [];
    foreach ($paths as $path) {
        $sourceId = pathinfo($path, PATHINFO_FILENAME);
        if ($sourceId === 'hsguru_meta_matrix'
            || $sourceId === 'hsreplay_battlegrounds_heroes'
            || $sourceId === 'hsreplay_battlegrounds_hero_details'
            || $sourceId === 'hsreplay_arena_cards_advanced'
            || strpos($sourceId, 'firestone_arena_cards_') === 0
            || preg_match('/^hsreplay_cards_(?:wild_)?(?:platinum|diamond|diamond_4_1|legend)_/', $sourceId)
        ) {
            $selected[] = $path;
        }
    }
    sort($selected, SORT_STRING);
    return $selected;
}

function statistics_cards_index(): array
{
    $body = file_get_contents(STATISTICS_CARDS_INDEX);
    if (!is_string($body)) {
        return [];
    }
    $payload = json_decode($body, true);
    $cards = is_array($payload['cards'] ?? null) ? $payload['cards'] : [];
    $index = [];
    foreach ($cards as $card) {
        if (!is_array($card) || empty($card['id'])) {
            continue;
        }
        $index[(string)$card['id']] = $card;
    }
    return $index;
}

function statistics_fetch_firestone_heroes(int $percentile, array $cards): array
{
    $url = sprintf(
        'https://static.zerotoheroes.com/api/bgs/hero-stats/mmr-%d/past-three/overview-from-hourly.gz.json',
        $percentile
    );
    $handle = curl_init($url);
    if ($handle === false) {
        throw new RuntimeException('Unable to initialize Firestone request');
    }
    curl_setopt_array($handle, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_TIMEOUT => 30,
        CURLOPT_ENCODING => '',
        CURLOPT_HTTPHEADER => ['Accept: application/json'],
        CURLOPT_USERAGENT => 'hs-data-platform-statistics/1.0',
    ]);
    $body = curl_exec($handle);
    $status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
    $error = curl_error($handle);
    curl_close($handle);
    if (!is_string($body) || $status < 200 || $status >= 300) {
        throw new RuntimeException($status > 0 ? 'Firestone HTTP ' . $status : 'Firestone: ' . $error);
    }
    $raw = json_decode($body, true, 512, JSON_THROW_ON_ERROR);
    $heroes = [];
    foreach (($raw['heroStats'] ?? []) as $row) {
        if (!is_array($row) || empty($row['heroCardId'])) {
            continue;
        }
        $cardId = (string)$row['heroCardId'];
        $card = $cards[$cardId] ?? [];
        $offered = statistics_number($row['totalOffered'] ?? null);
        $picked = statistics_number($row['totalPicked'] ?? null);
        $heroes[] = [
            'id' => $cardId,
            'hero_card_id' => $cardId,
            'dbfId' => $card['dbfId'] ?? null,
            'hero' => $card['name'] ?? $cardId,
            'avg_placement' => statistics_number($row['averagePosition'] ?? null),
            'pick_rate_value' => $offered && $picked !== null ? round(100 * $picked / $offered, 2) : null,
            'games' => statistics_integer($row['dataPoints'] ?? null),
            'mmr_percentile' => statistics_integer($row['mmrPercentile'] ?? $percentile),
            'total_offered' => statistics_integer($row['totalOffered'] ?? null),
            'total_picked' => statistics_integer($row['totalPicked'] ?? null),
            'conservative_position' => statistics_number($row['conservativePositionEstimate'] ?? null),
        ];
    }
    return [
        'source_id' => 'firestone_bg_heroes_mmr_' . $percentile,
        'version' => hash('sha256', $body),
        'payload' => [
            'fetched_at' => $raw['lastUpdateDate'] ?? gmdate(DATE_ATOM),
            'data' => ['structured' => [
                'type' => 'bg_heroes',
                'heroes' => $heroes,
                'mmr' => 'mmr-' . $percentile,
                'time_period' => 'past-three',
                'mode' => 'solo',
                'source' => [
                    'key' => 'firestone',
                    'url' => 'https://www.firestoneapp.com/battlegrounds/heroes',
                    'backend' => 'firestone_api',
                ],
            ]],
        ],
    ];
}

function statistics_emit_snapshot($snapshotStream, $rowStream, array $snapshot): array
{
    $identity = [
        $snapshot['source_id'], $snapshot['dataset_version'], $snapshot['domain'],
        $snapshot['entity_type'], $snapshot['format_name'], $snapshot['rank_range'],
        $snapshot['period'], $snapshot['mode'], $snapshot['rating_bracket'],
    ];
    $snapshotKey = hash('sha256', implode('|', array_map('strval', $identity)));
    statistics_write_csv($snapshotStream, [
        $snapshotKey,
        $snapshot['source_id'],
        $snapshot['dataset_version'],
        $snapshot['domain'],
        $snapshot['entity_type'],
        $snapshot['format_name'],
        $snapshot['rank_range'],
        $snapshot['period'],
        $snapshot['mode'],
        $snapshot['rating_bracket'],
        $snapshot['patch'],
        $snapshot['source_url'],
        $snapshot['fetched_at'],
        json_encode($snapshot['metadata'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR),
    ]);

    $rowCount = 0;
    foreach ($snapshot['rows'] as $row) {
        statistics_write_csv($rowStream, [
            $snapshotKey,
            $row['entity_key'],
            $row['entity_type'],
            $row['card_id'],
            $row['dbf_id'],
            $row['name'],
            $row['name_ru'],
            $row['class_name'],
            $row['tier'],
            $row['games'],
            $row['win_rate'],
            $row['popularity'],
            $row['pick_rate'],
            $row['avg_placement'],
            $row['score'],
            $row['image_url'],
            $row['source_url'],
            json_encode($row['metrics'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR),
        ]);
        $rowCount++;
    }
    return [1, $rowCount];
}

$runId = bin2hex(random_bytes(8));
$snapshotCsv = tmpfile();
$rowCsv = tmpfile();
if ($snapshotCsv === false || $rowCsv === false) {
    statistics_log('error', 'statistics_import_failed', $runId, ['error_code' => 'temporary_file_failed']);
    exit(1);
}

try {
    $snapshotCount = 0;
    $rowCount = 0;
    $sourceCount = 0;
    statistics_log('info', 'statistics_import_started', $runId);

    foreach (statistics_dataset_files() as $path) {
        $body = file_get_contents($path);
        if (!is_string($body)) {
            throw new RuntimeException('Unable to read ' . basename($path));
        }
        $payload = json_decode($body, true, 512, JSON_THROW_ON_ERROR);
        $sourceId = pathinfo($path, PATHINFO_FILENAME);
        $version = statistics_text($payload['dataset_version'] ?? null) ?? hash('sha256', $body);
        $snapshots = normalize_statistics_dataset($sourceId, $version, $payload);
        if ($snapshots !== []) {
            $sourceCount++;
        }
        foreach ($snapshots as $snapshot) {
            [$snapshotsAdded, $rowsAdded] = statistics_emit_snapshot($snapshotCsv, $rowCsv, $snapshot);
            $snapshotCount += $snapshotsAdded;
            $rowCount += $rowsAdded;
        }
    }

    $cards = statistics_cards_index();
    foreach ([100, 50, 25, 10, 1] as $percentile) {
        try {
            $source = statistics_fetch_firestone_heroes($percentile, $cards);
            $snapshots = normalize_statistics_dataset($source['source_id'], $source['version'], $source['payload']);
            foreach ($snapshots as $snapshot) {
                [$snapshotsAdded, $rowsAdded] = statistics_emit_snapshot($snapshotCsv, $rowCsv, $snapshot);
                $snapshotCount += $snapshotsAdded;
                $rowCount += $rowsAdded;
            }
            $sourceCount++;
        } catch (Throwable $exception) {
            statistics_log('warning', 'statistics_source_skipped', $runId, [
                'source_id' => 'firestone_bg_heroes_mmr_' . $percentile,
                'reason' => $exception->getMessage(),
            ]);
        }
    }

    if ($snapshotCount === 0 || $rowCount === 0) {
        throw new RuntimeException('No normalized statistics were produced');
    }

    rewind($snapshotCsv);
    rewind($rowCsv);
    [$process, $pipes] = statistics_open_target();
    fwrite($pipes[0], <<<'SQL'
BEGIN;
CREATE TEMP TABLE statistics_snapshot_import (
    snapshot_key text,
    source_id text,
    dataset_version text,
    domain text,
    entity_type text,
    format_name text,
    rank_range text,
    period text,
    mode text,
    rating_bracket text,
    patch text,
    source_url text,
    fetched_at timestamptz,
    metadata jsonb
) ON COMMIT DROP;
COPY statistics_snapshot_import FROM STDIN WITH (FORMAT csv);
SQL
    );
    fwrite($pipes[0], PHP_EOL);
    stream_copy_to_stream($snapshotCsv, $pipes[0]);
    fwrite($pipes[0], "\\.\n");
    fwrite($pipes[0], <<<'SQL'
CREATE TEMP TABLE statistics_row_import (
    snapshot_key text,
    entity_key text,
    entity_type text,
    card_id text,
    dbf_id bigint,
    name text,
    name_ru text,
    class_name text,
    tier text,
    games bigint,
    win_rate numeric,
    popularity numeric,
    pick_rate numeric,
    avg_placement numeric,
    score numeric,
    image_url text,
    source_url text,
    metrics jsonb
) ON COMMIT DROP;
COPY statistics_row_import FROM STDIN WITH (FORMAT csv);
SQL
    );
    fwrite($pipes[0], PHP_EOL);
    stream_copy_to_stream($rowCsv, $pipes[0]);
    fwrite($pipes[0], "\\.\n");
    fwrite($pipes[0], <<<'SQL'
INSERT INTO analytics.game_stat_snapshots (
    snapshot_key, source_id, dataset_version, domain, entity_type,
    format_name, rank_range, period, mode, rating_bracket, patch,
    source_url, fetched_at, metadata
)
SELECT DISTINCT ON (snapshot_key)
    snapshot_key, source_id, dataset_version, domain, entity_type,
    format_name, rank_range, period, mode, rating_bracket, patch,
    source_url, fetched_at, metadata
FROM statistics_snapshot_import
ORDER BY snapshot_key, fetched_at DESC
ON CONFLICT (snapshot_key) DO UPDATE SET
    source_url = EXCLUDED.source_url,
    fetched_at = EXCLUDED.fetched_at,
    imported_at = now(),
    metadata = EXCLUDED.metadata;

DELETE FROM analytics.game_stat_rows AS target
USING analytics.game_stat_snapshots AS snapshot,
      (SELECT DISTINCT snapshot_key FROM statistics_snapshot_import) AS imported
WHERE target.snapshot_id = snapshot.id
  AND snapshot.snapshot_key = imported.snapshot_key;

INSERT INTO analytics.game_stat_rows (
    snapshot_id, entity_key, entity_type, card_id, dbf_id, name, name_ru,
    class_name, tier, games, win_rate, popularity, pick_rate, avg_placement,
    score, image_url, source_url, metrics
)
SELECT DISTINCT ON (snapshot.id, row.entity_key)
    snapshot.id, row.entity_key, row.entity_type, row.card_id, row.dbf_id,
    row.name, row.name_ru, row.class_name, row.tier, row.games, row.win_rate,
    row.popularity, row.pick_rate, row.avg_placement, row.score, row.image_url,
    row.source_url, row.metrics
FROM statistics_row_import AS row
JOIN analytics.game_stat_snapshots AS snapshot ON snapshot.snapshot_key = row.snapshot_key
ORDER BY snapshot.id, row.entity_key;

UPDATE platform.data_sources
SET last_synced_at = now(), updated_at = now()
WHERE source_id = 'statistics-normalized';

INSERT INTO platform.sync_runs (
    source_id, completed_at, state, rows_read, rows_written, details
)
SELECT
    'statistics-normalized', now(), 'ok', count(*), count(*),
    jsonb_build_object(
        'mode', 'normalized_snapshot',
        'snapshots', (SELECT count(*) FROM statistics_snapshot_import)
    )
FROM statistics_row_import;
COMMIT;
SQL
    );
    fwrite($pipes[0], PHP_EOL);
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]);
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $exitCode = proc_close($process);
    if ($exitCode !== 0) {
        throw new RuntimeException(trim((string)$stderr) ?: 'PostgreSQL statistics import failed');
    }

    statistics_log('info', 'statistics_import_completed', $runId, [
        'source_count' => $sourceCount,
        'snapshot_count' => $snapshotCount,
        'row_count' => $rowCount,
    ]);
} catch (Throwable $exception) {
    statistics_log('error', 'statistics_import_failed', $runId, [
        'error_code' => 'import_failed',
        'error_message' => $exception->getMessage(),
    ]);
    exit(1);
} finally {
    fclose($snapshotCsv);
    fclose($rowCsv);
}
