<?php
declare(strict_types=1);
require __DIR__ . '/../lib/panel_assets.php';

function check_assets(bool $ok, string $message): void {
    if (!$ok) throw new RuntimeException($message);
}
$common = ['workspace', 'panel-ui'];
$modules = [
    'list' => ['table-controls', 'media-preview'],
    'analytics' => ['table-controls', 'parsing-reliability', 'analytics'],
    'parsers' => ['parser-control-view', 'parser-control'],
    'api_tokens' => ['table-controls', 'token-controls'],
    'new' => ['editor-controls'],
    'edit' => ['editor-controls'],
    'wiki_terms' => ['editor-controls'],
    'unknown' => [],
];
foreach ($modules as $action => $expected) {
    $assets = panel_script_assets($action);
    $names = array_map(static function (string $asset): string {
        return basename(explode('?', $asset)[0], '.js');
    }, $assets);
    check_assets($names === array_merge($common, $expected), 'Only required scripts, in dependency order: ' . $action);
    check_assets(count($assets) === count(array_unique($assets)), 'No duplicate scripts');
    $bytes = 0;
    foreach ($assets as $asset) {
        $file = __DIR__ . '/..' . explode('?', $asset)[0];
        check_assets(is_file($file), 'Script exists: ' . $asset);
        check_assets((bool)preg_match('~^/assets/[a-z-]+\.js\?v=\d+$~', $asset), 'Versioned local asset');
        $bytes += filesize($file);
    }
    if ($action === 'list') check_assets($bytes < 26000, 'Catalogue JS raw budget: 26KB');
    echo $action . ': scripts=' . count($assets) . ' bytes=' . $bytes . "\n";
}
$source = file_get_contents(__DIR__ . '/../index.php');
check_assets(strpos($source, "partials/page-assets.php") !== false, 'Production uses the tested asset manifest');
check_assets(!preg_match('~<script\s+src=~', $source), 'No unconditional scripts outside the manifest');
echo "OK: section-specific asset budgets\n";
