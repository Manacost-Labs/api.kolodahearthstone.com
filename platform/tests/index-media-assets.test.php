<?php
declare(strict_types=1);

// The scheduler holds the shared sync lock while this indexer runs, so a walk
// that enters the S3 mount or follows offloaded links blocks every other job.
require __DIR__ . '/../scripts/index-media-assets.php';

function check(bool $condition, string $message): void
{
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}" . PHP_EOL);
        exit(1);
    }
}

$base = sys_get_temp_dir() . '/media-index-test-' . bin2hex(random_bytes(4));
mkdir($base . '/uploads/.s3-storage/golden', 0700, true);
mkdir($base . '/uploads/golden', 0700, true);
mkdir($base . '/outside', 0700, true);
$root = realpath($base . '/uploads');
$mount = $root . '/' . OFFLOAD_MOUNT;

file_put_contents($root . '/golden/local.png', str_repeat('x', 7));
touch($root . '/golden/local.png', 1_700_000_000);
// Remote copies exist only behind the mount.
file_put_contents($mount . '/golden/known.png', str_repeat('k', 11));
file_put_contents($mount . '/golden/new.png', str_repeat('n', 13));
symlink($mount . '/golden/known.png', $root . '/golden/known.png');
symlink($mount . '/golden/new.png', $root . '/golden/new.png');
symlink($mount . '/golden/gone.png', $root . '/golden/gone.png');
file_put_contents($base . '/outside/foreign.png', 'f');
symlink($base . '/outside/foreign.png', $root . '/golden/foreign.png');
symlink($base . '/outside', $root . '/linked-dir');

$remoteCalls = [];
$remoteStat = static function (string $path) use (&$remoteCalls): ?array {
    $remoteCalls[] = $path;
    $stat = @stat($path);
    return $stat === false ? null : [(int)$stat['size'], (int)$stat['mtime']];
};

$stats = ['remote_stats' => 0, 'unresolved' => 0];
$rows = [];
foreach (collectMediaRows($root, ['golden/known.png' => true], $remoteStat, $stats) as [$path, $size, $mtime]) {
    $rows[$path] = [$size, $mtime];
}
ksort($rows);

check(array_keys($rows) === ['golden/known.png', 'golden/local.png', 'golden/new.png'], 'unexpected rows: ' . json_encode(array_keys($rows)));
check($rows['golden/local.png'] === [7, 1_700_000_000], 'local files must keep their own size and mtime');
check($rows['golden/known.png'] === [null, null], 'indexed offloaded files must reuse indexed metadata without a remote stat');
check($rows['golden/new.png'][0] === 13, 'unindexed offloaded files are stat()ed once through the mount');
check($remoteCalls === [$mount . '/golden/new.png', $mount . '/golden/gone.png'] || $remoteCalls === [$mount . '/golden/gone.png', $mount . '/golden/new.png'], 'only unknown offloaded links may reach the mount: ' . json_encode($remoteCalls));
check($stats === ['remote_stats' => 2, 'unresolved' => 1], 'a missing remote object is counted, not indexed: ' . json_encode($stats));
foreach (array_keys($rows) as $path) {
    check(!str_starts_with($path, OFFLOAD_MOUNT), 'the S3 mount must never be walked');
}

$limited = ['remote_stats' => 0, 'unresolved' => 0];
$remoteCalls = [];
iterator_to_array(collectMediaRows($root, [], $remoteStat, $limited, 1), false);
check(count($remoteCalls) === 1, 'remote stats must respect the per-run limit');
check($limited['unresolved'] === 2, 'links beyond the limit wait for a later run');

exec('rm -rf ' . escapeshellarg($base));
echo 'OK: media index skips the S3 mount' . PHP_EOL;
