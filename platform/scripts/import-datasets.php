#!/usr/bin/env php
<?php
declare(strict_types=1);

const PLATFORM_DIR = __DIR__ . '/..';
const DATASET_DIR = '/srv/hs-data-api/data/datasets';

function logEvent(string $level, string $event, string $runId, array $context = []): void
{
    $record = array_merge([
        'timestamp' => gmdate('c'),
        'level' => $level,
        'event' => $event,
        'run_id' => $runId,
    ], $context);
    $stream = $level === 'error' ? STDERR : STDOUT;
    fwrite($stream, json_encode($record, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR) . PHP_EOL);
}

function openTarget(): array
{
    $composeDir = PLATFORM_DIR . '/postgres';
    $process = proc_open(
        [
            '/usr/bin/sudo',
            '-n',
            'docker',
            'compose',
            '--project-directory',
            $composeDir,
            '-f',
            $composeDir . '/docker-compose.yml',
            'exec',
            '-T',
            'postgres',
            'sh',
            '-eu',
            '-c',
            'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data',
        ],
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
        $pipes,
        null,
        null,
        ['bypass_shell' => true],
    );
    if (!is_resource($process)) {
        throw new RuntimeException('Unable to start PostgreSQL import process');
    }
    return [$process, $pipes];
}

function writeCsvRow($stream, array $row): void
{
    $temporary = fopen('php://temp', 'w+');
    if ($temporary === false) {
        throw new RuntimeException('Unable to create CSV buffer');
    }
    fputcsv($temporary, $row, ',', '"', '');
    rewind($temporary);
    stream_copy_to_stream($temporary, $stream);
    fclose($temporary);
}

$files = glob(DATASET_DIR . '/*.json') ?: [];
sort($files, SORT_STRING);
$runId = bin2hex(random_bytes(8));
if ($files === []) {
    logEvent('error', 'dataset_import_failed', $runId, ['error_code' => 'no_datasets']);
    exit(1);
}

try {
    logEvent('info', 'dataset_import_started', $runId, ['source_count' => count($files)]);
    [$process, $pipes] = openTarget();
    fwrite($pipes[0], <<<'SQL'
BEGIN;
CREATE TEMP TABLE dataset_import (
    source_id text,
    dataset_version text,
    fetched_at timestamptz,
    state text,
    payload jsonb
) ON COMMIT DROP;
COPY dataset_import (source_id, dataset_version, fetched_at, state, payload)
FROM STDIN WITH (FORMAT csv);
SQL
    );
    fwrite($pipes[0], PHP_EOL);

    $imported = 0;
    foreach ($files as $file) {
        $json = file_get_contents($file);
        if (!is_string($json)) {
            throw new RuntimeException("Unable to read {$file}");
        }
        $payload = json_decode($json, true, flags: JSON_THROW_ON_ERROR);
        if (!is_array($payload)) {
            throw new RuntimeException("Dataset is not a JSON object: {$file}");
        }

        $sourceId = pathinfo($file, PATHINFO_FILENAME);
        $version = (string)($payload['dataset_version'] ?? hash('sha256', $json));
        $fetchedAt = isset($payload['fetched_at']) ? (string)$payload['fetched_at'] : null;
        $state = isset($payload['state']) ? (string)$payload['state'] : null;
        writeCsvRow($pipes[0], [$sourceId, $version, $fetchedAt, $state, $json]);
        $imported++;
    }

    fwrite($pipes[0], "\\." . PHP_EOL);
    fwrite($pipes[0], <<<'SQL'
INSERT INTO raw.datasets (source_id, dataset_version, fetched_at, state, payload)
SELECT source_id, dataset_version, fetched_at, state, payload
FROM dataset_import
ON CONFLICT (source_id, dataset_version) DO UPDATE SET
    fetched_at = EXCLUDED.fetched_at,
    state = EXCLUDED.state,
    payload = EXCLUDED.payload,
    imported_at = now();

UPDATE platform.data_sources
SET last_synced_at = now(), updated_at = now()
WHERE source_id = 'parser-json';

INSERT INTO platform.sync_runs (
    source_id, completed_at, state, rows_read, rows_written, details
)
SELECT
    'parser-json', now(), 'ok', count(*), count(*),
    jsonb_build_object('mode', 'snapshot')
FROM dataset_import;
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
        throw new RuntimeException(trim((string)$stderr) ?: 'PostgreSQL dataset import failed');
    }
    logEvent('info', 'dataset_import_completed', $runId, ['source_count' => $imported]);
} catch (Throwable $exception) {
    logEvent('error', 'dataset_import_failed', $runId, [
        'error_code' => 'import_failed',
        'error_message' => $exception->getMessage(),
    ]);
    exit(1);
}
