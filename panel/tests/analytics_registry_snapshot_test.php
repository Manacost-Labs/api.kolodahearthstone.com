<?php
declare(strict_types=1);

// The Next.js fixture serves panel-next/tests/fixtures/analytics-registry.json as the PHP registry.
// A module added, removed or reworded in analytics_module_registry() must be mirrored there.
require dirname(__DIR__) . '/lib/analytics.php';

$modules = [];
foreach (analytics_module_registry() as $key => $definition) {
    $modules[$key] = array_intersect_key($definition, array_flip(['title', 'description', 'params']));
}
$snapshotPath = dirname(__DIR__, 2) . '/panel-next/tests/fixtures/analytics-registry.json';
$raw = is_file($snapshotPath) ? file_get_contents($snapshotPath) : false;
$snapshot = is_string($raw) ? json_decode($raw, true) : null;
$regenerate = 'php panel/tests/analytics_cli.php --registry > panel-next/tests/fixtures/analytics-registry.json';

if (!is_array($snapshot) || !isset($snapshot['modules']) || !is_array($snapshot['modules'])) {
    fwrite(STDERR, "Registry snapshot is missing or invalid. Regenerate: $regenerate\n");
    exit(1);
}
$missing = array_diff(array_keys($modules), array_keys($snapshot['modules']));
$extra = array_diff(array_keys($snapshot['modules']), array_keys($modules));
if ($missing !== [] || $extra !== [] || $snapshot['modules'] != $modules) {
    fwrite(STDERR, 'Registry snapshot is stale (missing: ' . implode(',', $missing) . '; extra: ' . implode(',', $extra) . "). Regenerate: $regenerate\n");
    exit(1);
}
if (array_keys($snapshot['modules']) !== array_keys($modules)) {
    fwrite(STDERR, "Registry snapshot order differs from PHP. Regenerate: $regenerate\n");
    exit(1);
}
echo "analytics registry snapshot ok\n";
