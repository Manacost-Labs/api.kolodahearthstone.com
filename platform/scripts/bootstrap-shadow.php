#!/usr/bin/env php
<?php
declare(strict_types=1);

const PLATFORM_DIR = __DIR__ . '/..';
const CATALOG_CONFIG = '/etc/api-kolodahearthstone/panel-config.php';
const ANALYTICS_DB = '/srv/hs-data-api/data/hs_parses.db';
const TARGET_CONFIG = PLATFORM_DIR . '/postgres/connection.json';

function fail(string $message): never
{
    fwrite(STDERR, $message . PHP_EOL);
    exit(1);
}

function connectionUri(string $scheme, string $user, string $password, string $host, int $port, string $database): string
{
    return sprintf(
        '%s://%s:%s@%s:%d/%s',
        $scheme,
        rawurlencode($user),
        rawurlencode($password),
        $host,
        $port,
        rawurlencode($database),
    );
}

function parseMysqlDsn(string $dsn): array
{
    if (!str_starts_with($dsn, 'mysql:')) {
        throw new RuntimeException('Catalogue DSN is not MySQL');
    }

    $parts = [];
    foreach (explode(';', substr($dsn, 6)) as $part) {
        if (!str_contains($part, '=')) {
            continue;
        }
        [$key, $value] = explode('=', $part, 2);
        $parts[trim($key)] = trim($value);
    }

    if (empty($parts['host']) || empty($parts['dbname'])) {
        throw new RuntimeException('Catalogue DSN must contain host and dbname');
    }

    return [
        'host' => $parts['host'] === 'localhost' ? '127.0.0.1' : $parts['host'],
        'port' => isset($parts['port']) ? (int)$parts['port'] : 3306,
        'database' => $parts['dbname'],
    ];
}

function runProcess(array $command, array $secrets = []): void
{
    $process = proc_open(
        $command,
        [1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
        $pipes,
        null,
        null,
        ['bypass_shell' => true],
    );
    if (!is_resource($process)) {
        throw new RuntimeException('Unable to start process');
    }

    $stdout = stream_get_contents($pipes[1]);
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $exitCode = proc_close($process);

    $output = trim((string)$stdout . PHP_EOL . (string)$stderr);
    foreach ($secrets as $secret) {
        if (is_string($secret) && $secret !== '') {
            $output = str_replace([$secret, rawurlencode($secret)], '[redacted]', $output);
        }
    }

    if ($exitCode !== 0) {
        throw new RuntimeException("Process failed with exit code {$exitCode}:\n{$output}");
    }

    if ($output !== '') {
        fwrite(STDOUT, $output . PHP_EOL);
    }
}

function createPublicAnalyticsSnapshot(): string
{
    $path = tempnam('/tmp', 'hs-data-public-analytics-');
    if ($path === false) {
        throw new RuntimeException('Unable to create temporary analytics snapshot');
    }

    try {
        chmod($path, 0600);
        runProcess(['/usr/bin/sqlite3', ANALYTICS_DB, '.backup ' . $path]);
        runProcess([
            '/usr/bin/sqlite3',
            $path,
            'DROP TABLE IF EXISTS api_tokens; ' .
            'DROP INDEX IF EXISTS idx_bg_minion_round_stats_snapshot; ' .
            'VACUUM;',
        ]);
        chmod($path, 0600);
        return $path;
    } catch (Throwable $exception) {
        @unlink($path);
        throw $exception;
    }
}

function runPgloader(string $label, string $specification, array $secrets): void
{
    $path = tempnam('/tmp', 'hs-data-pgloader-');
    if ($path === false) {
        throw new RuntimeException('Unable to create temporary pgloader file');
    }

    try {
        chmod($path, 0600);
        if (file_put_contents($path, $specification, LOCK_EX) === false) {
            throw new RuntimeException('Unable to write temporary pgloader file');
        }
        fwrite(STDOUT, "Loading {$label}..." . PHP_EOL);
        runProcess(
            [
                '/usr/bin/pgloader',
                '--dynamic-space-size',
                '4096',
                '--on-error-stop',
                '--client-min-messages=warning',
                $path,
            ],
            $secrets,
        );
        fwrite(STDOUT, "Loaded {$label}." . PHP_EOL);
    } finally {
        @unlink($path);
    }
}

function applyPostMigrationSql(): void
{
    $compose = PLATFORM_DIR . '/postgres/docker-compose.yml';
    $sql = PLATFORM_DIR . '/sql/002_jsonb_and_hub.sql';
    $command = sprintf(
        "sudo -n docker compose --project-directory %s -f %s exec -T postgres " .
        "sh -eu -c 'psql -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d hs_data' < %s",
        escapeshellarg(PLATFORM_DIR . '/postgres'),
        escapeshellarg($compose),
        escapeshellarg($sql),
    );
    runProcess(['/bin/bash', '-lc', $command]);
}

$mode = $argv[1] ?? 'all';
if (!in_array($mode, ['catalog', 'analytics', 'all', 'stage'], true)) {
    fail('Usage: bootstrap-shadow.php [catalog|analytics|all|stage]');
}

$catalogSchema = $mode === 'stage' ? 'catalog_stage' : 'catalog';
$analyticsSchema = $mode === 'stage' ? 'analytics_stage' : 'analytics';

$catalogConfig = require CATALOG_CONFIG;
$targetConfig = json_decode((string)file_get_contents(TARGET_CONFIG), true, flags: JSON_THROW_ON_ERROR);

$target = $targetConfig['connection'] ?? null;
if (!is_array($target) || empty($target['user']) || !array_key_exists('password', $target)) {
    fail('Invalid PostgreSQL target configuration');
}

$targetUri = connectionUri(
    'postgresql',
    (string)$target['user'],
    (string)$target['password'],
    '127.0.0.1',
    15434,
    'hs_data',
);

$analyticsSnapshot = null;
try {
    try {
        if ($mode === 'catalog' || $mode === 'all' || $mode === 'stage') {
            $mysql = parseMysqlDsn((string)$catalogConfig['db']['dsn']);
            $sourceUri = connectionUri(
                'mysql',
                (string)$catalogConfig['db']['user'],
                (string)$catalogConfig['db']['password'],
                $mysql['host'],
                $mysql['port'],
                $mysql['database'],
            );

            $specification = <<<PGLOAD
LOAD DATABASE
     FROM {$sourceUri}
     INTO {$targetUri}

 WITH include drop, create tables, create indexes, reset sequences, foreign keys,
      workers = 4, concurrency = 2, multiple readers per thread, rows per range = 50000

 SET PostgreSQL PARAMETERS
     maintenance_work_mem to '1GB',
     work_mem to '64MB'

 SET MySQL PARAMETERS
     net_read_timeout = '120',
     net_write_timeout = '120'

 CAST type datetime to timestamptz drop default using zero-dates-to-null,
      type date drop default using zero-dates-to-null,
      type year to integer drop typemod

 ALTER TABLE NAMES MATCHING ~/./ SET SCHEMA '{$catalogSchema}';
PGLOAD;

            runPgloader(
                'MariaDB catalogues',
                $specification,
                [(string)$catalogConfig['db']['password'], (string)$target['password']],
            );
        }

        if ($mode === 'analytics' || $mode === 'all' || $mode === 'stage') {
            if (!is_file(ANALYTICS_DB)) {
                throw new RuntimeException('Analytics SQLite database is missing');
            }
            $analyticsSnapshot = createPublicAnalyticsSnapshot();
            $sqliteUri = 'sqlite://' . $analyticsSnapshot;
            $specification = <<<PGLOAD
LOAD DATABASE
     FROM {$sqliteUri}
     INTO {$targetUri}

 WITH include drop, create tables, create indexes, reset sequences

 SET PostgreSQL PARAMETERS
     maintenance_work_mem to '1GB',
     work_mem to '64MB'

 INCLUDING ONLY TABLE NAMES LIKE 'archetype_deck_cards', 'archetype_decks',
                                 'archetype_matchups', 'archetype_mulligan',
                                 'archetype_refresh_runs', 'archetype_snapshots',
                                 'archetype_time_series', 'bg_minion_refresh_runs',
                                 'bg_minion_round_stats', 'bg_minion_snapshots',
                                 'bg_minions', 'card_popularity_history', 'decks',
                                 'fetch_log', 'hearthstone_patches',
                                 'hsguru_archetype_history', 'hsreplay_archetypes'
 EXCLUDING TABLE NAMES LIKE 'api_tokens'

 ALTER TABLE NAMES MATCHING ~/./ SET SCHEMA '{$analyticsSchema}';
PGLOAD;

            runPgloader('SQLite analytics', $specification, [(string)$target['password']]);
        }

        if ($mode === 'all') {
            applyPostMigrationSql();
        }
    } finally {
        if (is_string($analyticsSnapshot)) {
            @unlink($analyticsSnapshot);
        }
    }
} catch (Throwable $exception) {
    fail($exception->getMessage());
}

fwrite(STDOUT, 'Shadow bootstrap completed.' . PHP_EOL);
