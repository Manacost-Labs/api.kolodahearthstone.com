#!/usr/bin/env php
<?php
declare(strict_types=1);

const PLATFORM_DIR = __DIR__ . '/..';
const MEDIA_ROOT = '/srv/api-kolodahearthstone/panel-data/uploads';

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

$root = realpath(MEDIA_ROOT);
if ($root === false || !is_dir($root)) {
    fwrite(STDERR, 'Media root is unavailable.' . PHP_EOL);
    exit(1);
}

$composeDir = PLATFORM_DIR . '/postgres';
$command = [
    '/usr/bin/sudo', '-n', 'docker', 'compose',
    '--project-directory', $composeDir,
    '-f', $composeDir . '/docker-compose.yml',
    'exec', '-T', 'postgres', 'sh', '-eu', '-c',
    'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data',
];
$process = proc_open(
    $command,
    [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
    $pipes,
    null,
    null,
    ['bypass_shell' => true],
);
if (!is_resource($process)) {
    fwrite(STDERR, 'Unable to start PostgreSQL media import.' . PHP_EOL);
    exit(1);
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
$totalBytes = 0;
$directory = new RecursiveDirectoryIterator(
    $root,
    FilesystemIterator::SKIP_DOTS | FilesystemIterator::CURRENT_AS_FILEINFO,
);
$iterator = new RecursiveIteratorIterator($directory, RecursiveIteratorIterator::LEAVES_ONLY);
foreach ($iterator as $file) {
    if (!$file->isFile() || $file->isLink()) {
        continue;
    }
    $pathname = $file->getPathname();
    $relativePath = str_replace(DIRECTORY_SEPARATOR, '/', substr($pathname, strlen($root) + 1));
    $assetGroup = explode('/', $relativePath, 2)[0];
    $size = $file->getSize();
    $modifiedAt = gmdate('Y-m-d\TH:i:s\Z', $file->getMTime());
    fwrite($input, implode("\t", [
        copyEscape($relativePath),
        copyEscape($assetGroup),
        copyEscape(mediaType($relativePath)),
        (string)$size,
        $modifiedAt,
    ]) . PHP_EOL);
    $count++;
    $totalBytes += $size;
}

fwrite($input, <<<'SQL'
\.
TRUNCATE platform.media_assets;
INSERT INTO platform.media_assets (
    relative_path, asset_group, media_type, size_bytes, modified_at, indexed_at
)
SELECT relative_path, asset_group, media_type, size_bytes, modified_at, now()
  FROM media_assets_import;
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
    exit($exitCode);
}
if (trim((string)$stdout) !== '') {
    fwrite(STDOUT, trim((string)$stdout) . PHP_EOL);
}

printf("Indexed media assets: %d files, %d bytes\n", $count, $totalBytes);
