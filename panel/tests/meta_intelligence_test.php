<?php
declare(strict_types=1);

require __DIR__ . '/../lib/meta_intelligence.php';

function expect_true(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

[$path, $query] = panel_meta_intelligence_request('archetypes', [
    'format' => 'wild',
    'rank' => 'legend',
    'period' => 'past_week',
    'patch' => 'unknown',
    'q' => 'Dragon',
    'limit' => '9999',
]);
expect_true($path === '/v1/meta-intelligence/archetypes', 'Archetype path must be fixed.');
expect_true($query['format'] === 'wild' && $query['limit'] === 500, 'Scope and limit must be normalized.');

[$detailPath] = panel_meta_intelligence_request('archetype', [
    'archetype' => 'Attack Druid',
    'format' => 'standard',
    'rank' => 'legend',
    'period' => 'past_week',
    'patch' => 'unknown',
]);
expect_true($detailPath === '/v1/meta-intelligence/archetypes/Attack%20Druid', 'Archetype must be encoded as one path segment.');

$rejected = false;
try {
    panel_meta_intelligence_request('archetype', ['archetype' => "Bad\nName"]);
} catch (InvalidArgumentException $exception) {
    $rejected = true;
}
expect_true($rejected, 'Control characters must be rejected.');

$rejected = false;
try {
    panel_meta_intelligence_request('compare', [
        'archetype' => 'Attack Druid',
        'left' => '../../secret',
        'right' => str_repeat('a', 64),
    ]);
} catch (InvalidArgumentException $exception) {
    $rejected = true;
}
expect_true($rejected, 'Deck identifiers must be exact hashes.');

$rejected = false;
try {
    panel_meta_intelligence_request('https://example.com', []);
} catch (InvalidArgumentException $exception) {
    $rejected = true;
}
expect_true($rejected, 'Arbitrary proxy routes must be rejected.');

ob_start();
require __DIR__ . '/../partials/meta-intelligence.php';
$markup = (string)ob_get_clean();
expect_true(strpos($markup, 'data-meta-lab') !== false, 'Meta Lab workspace must render in the panel.');
expect_true(strpos($markup, '/analytics.php') !== false, 'Meta Lab must use the authenticated panel gateway.');
expect_true(strpos($markup, 'X-API-Key') === false && strpos($markup, 'type="password"') === false, 'The browser markup must not request or expose an API key.');

echo "meta intelligence panel gateway tests: ok\n";
