#!/usr/bin/env php
<?php
declare(strict_types=1);

const PLATFORM_DIR = __DIR__ . '/..';
const CATALOG_CONFIG = '/etc/api-kolodahearthstone/panel-config.php';
const ANALYTICS_DB = '/srv/hs-data-api/data/hs_parses.db';
const PRIVATE_ANALYTICS_TABLES = ['api_tokens', 'api_token_usage_monthly'];

function runProcess(array $command, string $stdin = ''): string
{
    $process = proc_open(
        $command,
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
        $pipes,
        null,
        null,
        ['bypass_shell' => true],
    );
    if (!is_resource($process)) {
        throw new RuntimeException('Unable to start verification process');
    }

    fwrite($pipes[0], $stdin);
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]);
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $exitCode = proc_close($process);
    if ($exitCode !== 0) {
        throw new RuntimeException(trim((string)$stderr) ?: 'Verification process failed');
    }

    return trim((string)$stdout);
}

function targetScalar(string $sql): string
{
    $composeDir = PLATFORM_DIR . '/postgres';
    return runProcess(
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
            'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data -At',
        ],
        $sql . PHP_EOL,
    );
}

function safeIdentifier(string $identifier): string
{
    if (!preg_match('/^[A-Za-z0-9_]+$/', $identifier)) {
        throw new RuntimeException("Unsafe database identifier: {$identifier}");
    }
    return '"' . $identifier . '"';
}

function sqliteScalar(string $sql): string
{
    return runProcess(['/usr/bin/sqlite3', '-noheader', ANALYTICS_DB, $sql]);
}

function analyticsSourceTables(): array
{
    $tables = array_filter(explode(PHP_EOL, sqliteScalar(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
    )));

    return array_values(array_filter(
        $tables,
        static fn(string $table): bool => !in_array($table, PRIVATE_ANALYTICS_TABLES, true),
    ));
}

function sourceMysql(): PDO
{
    $config = require CATALOG_CONFIG;
    return new PDO(
        $config['db']['dsn'],
        $config['db']['user'],
        $config['db']['password'],
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC],
    );
}

function compareSchemaCounts(PDO $mysql): array
{
    $mismatches = [];
    $warnings = [];
    $appendOnlyTables = [
        'battlegrounds_card_changes',
        'battlegrounds_card_import_runs',
        'constructed_import_runs',
        'jobs',
    ];
    $sourceTotal = 0;
    $targetTotal = 0;
    $tables = $mysql->query(
        "SELECT table_name FROM information_schema.tables " .
        "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name"
    )->fetchAll(PDO::FETCH_COLUMN);

    foreach ($tables as $table) {
        $quotedMysql = '`' . str_replace('`', '``', (string)$table) . '`';
        $sourceCount = (int)$mysql->query("SELECT COUNT(*) FROM {$quotedMysql}")->fetchColumn();
        $targetCount = (int)targetScalar(
            'SELECT COUNT(*) FROM catalog.' . safeIdentifier((string)$table) . ';'
        );
        $sourceTotal += $sourceCount;
        $targetTotal += $targetCount;
        if ($sourceCount !== $targetCount) {
            if (in_array((string)$table, $appendOnlyTables, true) && $targetCount <= $sourceCount) {
                $warnings[] = "catalog.{$table}: shadow lag=" . ($sourceCount - $targetCount);
            } else {
                $mismatches[] = "catalog.{$table}: source={$sourceCount}, target={$targetCount}";
            }
        }
    }

    return [$mismatches, $warnings, $sourceTotal, $targetTotal, count($tables)];
}

function compareSqliteCounts(): array
{
    $mismatches = [];
    $warnings = [];
    $appendOnlyTables = [
        'archetype_deck_cards',
        'archetype_decks',
        'archetype_matchups',
        'archetype_mulligan',
        'archetype_refresh_runs',
        'archetype_snapshots',
        'archetype_time_series',
        'bg_minion_refresh_runs',
        'bg_minion_round_stats',
        'bg_minion_snapshots',
        'card_popularity_history',
        'fetch_log',
        'hsguru_archetype_history',
    ];
    $sourceTotal = 0;
    $targetTotal = 0;
    $tables = analyticsSourceTables();

    foreach ($tables as $table) {
        safeIdentifier($table);
        $sourceCount = (int)sqliteScalar('SELECT COUNT(*) FROM "' . $table . '";');
        $targetCount = (int)targetScalar(
            'SELECT COUNT(*) FROM analytics.' . safeIdentifier($table) . ';'
        );
        $sourceTotal += $sourceCount;
        $targetTotal += $targetCount;
        if ($sourceCount !== $targetCount) {
            if (in_array($table, $appendOnlyTables, true) && $targetCount <= $sourceCount) {
                $warnings[] = "analytics.{$table}: shadow lag=" . ($sourceCount - $targetCount);
            } else {
                $mismatches[] = "analytics.{$table}: source={$sourceCount}, target={$targetCount}";
            }
        }
    }

    return [$mismatches, $warnings, $sourceTotal, $targetTotal, count($tables)];
}

function verifyPrimaryKeys(PDO $mysql): array
{
    $mismatches = [];
    $sourceKeys = $mysql->query(
        "SELECT DISTINCT table_name FROM information_schema.key_column_usage " .
        "WHERE table_schema = DATABASE() AND constraint_name = 'PRIMARY'"
    )->fetchAll(PDO::FETCH_COLUMN);
    foreach ($sourceKeys as $table) {
        $exists = targetScalar(sprintf(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint c " .
            "JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace " .
            "WHERE c.contype = 'p' AND n.nspname = 'catalog' AND t.relname = '%s');",
            str_replace("'", "''", (string)$table),
        ));
        if ($exists !== 't') {
            $mismatches[] = "catalog.{$table}: missing primary key";
        }
    }

    $sqliteTables = analyticsSourceTables();
    foreach ($sqliteTables as $table) {
        $sourceHasKey = (int)sqliteScalar(
            "SELECT COUNT(*) FROM pragma_table_info('" . str_replace("'", "''", $table) . "') WHERE pk > 0;"
        ) > 0;
        if (!$sourceHasKey) {
            continue;
        }
        $exists = targetScalar(sprintf(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint c " .
            "JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace " .
            "WHERE c.contype = 'p' AND n.nspname = 'analytics' AND t.relname = '%s');",
            str_replace("'", "''", $table),
        ));
        if ($exists !== 't') {
            $mismatches[] = "analytics.{$table}: missing primary key";
        }
    }

    return $mismatches;
}

try {
    $mysql = sourceMysql();
    [$catalogMismatches, $warnings, $catalogSource, $catalogTarget, $catalogTables] = compareSchemaCounts($mysql);
    [$analyticsMismatches, $analyticsWarnings, $analyticsSource, $analyticsTarget, $analyticsTables] = compareSqliteCounts();
    $warnings = array_merge($warnings, $analyticsWarnings);
    $keyMismatches = verifyPrimaryKeys($mysql);

    $jsonTypeErrors = (int)targetScalar(
        "SELECT COUNT(*) FROM information_schema.columns " .
        "WHERE table_schema IN ('catalog', 'analytics') " .
        "AND (column_name LIKE '%\\_json' ESCAPE '\\' " .
        "OR column_name IN ('raw_json', 'raw_card_json', 'raw_deck_list', 'raw_deck_sideboard')) " .
        "AND data_type <> 'jsonb';"
    );
    $hubRows = (int)targetScalar('SELECT COUNT(*) FROM hub.card_catalog;');
    $rawSources = (int)targetScalar('SELECT COUNT(DISTINCT source_id) FROM raw.datasets;');
    $datasetFiles = count(glob('/srv/hs-data-api/data/datasets/*.json') ?: []);

    $errors = array_merge($catalogMismatches, $analyticsMismatches, $keyMismatches);
    if ($jsonTypeErrors > 0) {
        $errors[] = "{$jsonTypeErrors} JSON columns are not JSONB";
    }
    if ($hubRows <= 0) {
        $errors[] = 'hub.card_catalog is empty';
    }
    if ($rawSources !== $datasetFiles) {
        $errors[] = "raw datasets: files={$datasetFiles}, imported sources={$rawSources}";
    }

    printf("Catalog: %d tables, %d/%d rows\n", $catalogTables, $catalogTarget, $catalogSource);
    printf("Analytics: %d tables, %d/%d rows\n", $analyticsTables, $analyticsTarget, $analyticsSource);
    printf("Unified card catalogue: %d rows\n", $hubRows);
    printf("Raw parser datasets: %d/%d sources\n", $rawSources, $datasetFiles);
    foreach ($warnings as $warning) {
        fwrite(STDOUT, "WARNING: {$warning}" . PHP_EOL);
    }

    if ($errors !== []) {
        foreach ($errors as $error) {
            fwrite(STDERR, "ERROR: {$error}" . PHP_EOL);
        }
        exit(1);
    }

    fwrite(STDOUT, 'Data verification passed.' . PHP_EOL);
} catch (Throwable $exception) {
    fwrite(STDERR, 'Verification failed: ' . $exception->getMessage() . PHP_EOL);
    exit(1);
}
