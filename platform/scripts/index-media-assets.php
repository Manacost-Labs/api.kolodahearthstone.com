#!/usr/bin/env php
<?php
declare(strict_types=1);

require_once __DIR__ . '/sync-context.php';

const PLATFORM_DIR = __DIR__ . '/..';
const MEDIA_ROOT = '/srv/api-kolodahearthstone/panel-data/uploads';
// api-uploads-offload replaces verified files with links into this rclone
// mount. Every lookup inside it is an S3 request, so the walk never enters it.
const OFFLOAD_MOUNT = '.s3-storage';
// Offloaded files already in the index keep their recorded size and mtime.
// Only links the index has never seen are stat()ed through the mount.
const REMOTE_STAT_LIMIT = 2000;

function copyEscape(string $value): string
{
    return str_replace(
        ["\\", "\t", "\n", "\r"],
        ["\\\\", "\\t", "\\n", "\\r"],
        $value,
    );
}

function mediaType(string $path): string
{
    return match (strtolower(pathinfo($path, PATHINFO_EXTENSION))) {
        'avif' => 'image/avif',
        'gif' => 'image/gif',
        'jpeg', 'jpg' => 'image/jpeg',
        'png' => 'image/png',
        'svg' => 'image/svg+xml',
        'webp' => 'image/webp',
        default => 'application/octet-stream',
    };
}

/**
 * Yield [relative_path, size|null, mtime|null] without touching the S3 mount
 * for files the index already knows. A null size means "keep the indexed
 * values"; the SQL merge resolves it from platform.media_assets.
 *
 * @param array<string, true> $known relative paths already indexed
 * @param callable(string): (array{0:int,1:int}|null) $remoteStat
 * @param array{remote_stats:int, unresolved:int} $stats
 */
function collectMediaRows(string $root, array $known, callable $remoteStat, array &$stats, int $remoteLimit = REMOTE_STAT_LIMIT): Generator
{
    $mount = $root . '/' . OFFLOAD_MOUNT;
    $directory = new RecursiveDirectoryIterator(
        $root,
        FilesystemIterator::SKIP_DOTS | FilesystemIterator::CURRENT_AS_FILEINFO,
    );
    // Without FOLLOW_SYMLINKS linked directories are leaves; the mount is a
    // real directory and must be pruned explicitly.
    $filter = new RecursiveCallbackFilterIterator(
        $directory,
        static fn(SplFileInfo $file): bool => $file->getPathname() !== $mount,
    );
    $iterator = new RecursiveIteratorIterator($filter, RecursiveIteratorIterator::LEAVES_ONLY);
    foreach ($iterator as $file) {
        $pathname = $file->getPathname();
        $relativePath = str_replace(DIRECTORY_SEPARATOR, '/', substr($pathname, strlen($root) + 1));
        // isLink() uses lstat(); check it before anything that follows links.
        if ($file->isLink()) {
            if (readlink($pathname) !== $mount . '/' . $relativePath) {
                continue;
            }
            if (isset($known[$relativePath])) {
                yield [$relativePath, null, null];
                continue;
            }
            if ($stats['remote_stats'] >= $remoteLimit) {
                $stats['unresolved']++;
                continue;
            }
            $stats['remote_stats']++;
            $remote = $remoteStat($mount . '/' . $relativePath);
            if ($remote === null) {
                $stats['unresolved']++;
                continue;
            }
            yield [$relativePath, $remote[0], $remote[1]];
            continue;
        }
        if (!$file->isFile()) {
            continue;
        }
        yield [$relativePath, $file->getSize(), $file->getMTime()];
    }
}

function psqlCommand(): array
{
    $composeDir = PLATFORM_DIR . '/postgres';
    return [
        '/usr/bin/sudo', '-n', 'docker', 'compose',
        '--project-directory', $composeDir,
        '-f', $composeDir . '/docker-compose.yml',
        'exec', '-e', 'PGAPPNAME=' . syncApplicationName(), '-T', 'postgres', 'sh', '-eu', '-c',
        'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data',
    ];
}

/** @return array<string, true> */
function indexedPaths(): array
{
    $process = proc_open(
        psqlCommand(),
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
        $pipes,
        null,
        null,
        ['bypass_shell' => true],
    );
    if (!is_resource($process)) {
        throw new RuntimeException('Unable to read the media index.');
    }
    fwrite($pipes[0], "COPY (SELECT relative_path FROM platform.media_assets) TO STDOUT;\n");
    fclose($pipes[0]);
    $known = [];
    while (($line = fgets($pipes[1])) !== false) {
        $path = rtrim($line, "\n");
        if (str_contains($path, '\\')) {
            $path = str_replace(['\\t', '\\n', '\\r', '\\\\'], ["\t", "\n", "\r", '\\'], $path);
        }
        $known[$path] = true;
    }
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    if (proc_close($process) !== 0) {
        throw new RuntimeException('Unable to read the media index: ' . trim((string)$stderr));
    }
    return $known;
}

function main(): int
{
    $root = realpath(MEDIA_ROOT);
    if ($root === false || !is_dir($root)) {
        fwrite(STDERR, 'Media root is unavailable.' . PHP_EOL);
        return 1;
    }
    $known = indexedPaths();

    $process = proc_open(
        psqlCommand(),
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
        $pipes,
        null,
        null,
        ['bypass_shell' => true],
    );
    if (!is_resource($process)) {
        fwrite(STDERR, 'Unable to start PostgreSQL media import.' . PHP_EOL);
        return 1;
    }

    $input = $pipes[0];
    fwrite($input, <<<'SQL'
BEGIN;
CREATE TEMP TABLE media_assets_import (
    relative_path text,
    asset_group text,
    media_type text,
    size_bytes bigint,
    modified_at timestamptz
) ON COMMIT DROP;
COPY media_assets_import (relative_path, asset_group, media_type, size_bytes, modified_at)
FROM STDIN;
SQL
    );
    fwrite($input, PHP_EOL);

    $count = 0;
    $offloaded = 0;
    $stats = ['remote_stats' => 0, 'unresolved' => 0];
    $remoteStat = static function (string $path): ?array {
        $stat = @stat($path);
        return $stat === false ? null : [(int)$stat['size'], (int)$stat['mtime']];
    };
    foreach (collectMediaRows($root, $known, $remoteStat, $stats) as [$relativePath, $size, $mtime]) {
        $assetGroup = explode('/', $relativePath, 2)[0];
        fwrite($input, implode("\t", [
            copyEscape($relativePath),
            copyEscape($assetGroup),
            copyEscape(mediaType($relativePath)),
            $size === null ? '\N' : (string)$size,
            $mtime === null ? '\N' : gmdate('Y-m-d\TH:i:s\Z', $mtime),
        ]) . PHP_EOL);
        $count++;
        if ($size === null) {
            $offloaded++;
        }
    }

    fwrite($input, <<<'SQL'
\.
CREATE TEMP TABLE media_assets_next ON COMMIT DROP AS
SELECT import.relative_path, import.asset_group, import.media_type,
       COALESCE(import.size_bytes, indexed.size_bytes) AS size_bytes,
       COALESCE(import.modified_at, indexed.modified_at) AS modified_at
  FROM media_assets_import AS import
  LEFT JOIN platform.media_assets AS indexed USING (relative_path);
TRUNCATE platform.media_assets;
INSERT INTO platform.media_assets (
    relative_path, asset_group, media_type, size_bytes, modified_at, indexed_at
)
SELECT relative_path, asset_group, media_type, size_bytes, modified_at, now()
  FROM media_assets_next
 WHERE size_bytes IS NOT NULL AND modified_at IS NOT NULL;
COMMIT;
SQL
    );
    fwrite($input, PHP_EOL);
    fclose($input);
    $stdout = stream_get_contents($pipes[1]);
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $exitCode = proc_close($process);
    if ($exitCode !== 0) {
        fwrite(STDERR, trim((string)$stderr) . PHP_EOL);
        return $exitCode;
    }
    if (trim((string)$stdout) !== '') {
        fwrite(STDOUT, trim((string)$stdout) . PHP_EOL);
    }

    printf(
        "Indexed media assets: %d files (%d offloaded from index, %d remote stats, %d unresolved)\n",
        $count,
        $offloaded,
        $stats['remote_stats'],
        $stats['unresolved'],
    );
    return 0;
}

if (realpath((string)($_SERVER['SCRIPT_FILENAME'] ?? '')) === __FILE__) {
    exit(main());
}
