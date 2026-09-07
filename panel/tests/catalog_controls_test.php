<?php
declare(strict_types=1);
function h($value): string { return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }
function check(bool $value, string $message): void { if (!$value) throw new RuntimeException($message); }
$_GET = ['q'=>'<img src=x onerror=alert(1)>', 'card_type'=>'minion', 'per_page'=>'25', 'pool'=>'0', 'page'=>'2'];
require __DIR__ . '/catalog_fixture_state.php';
ob_start();
require __DIR__ . '/../partials/catalog-controls.php';
$html = ob_get_clean();
check(!str_contains($html, '<img src=x'), 'Search value and chips must be escaped');
check(str_contains($html, '&lt;img src=x'), 'Search text remains available');
check(str_contains($html, 'class="catalog-more" open'), 'Active advanced filters are disclosed');
check(str_contains($html, 'data-default-hidden="1,2,3,4,5,11,13,14,15,16"'), 'BG technical columns are optional');
check(str_contains($html, 'name="pool"'), 'Zero is a valid pool filter');
$page = 2; $totalPages = 6; $pageWindowStart = 1; $pageWindowEnd = 4;
ob_start();
require __DIR__ . '/../partials/catalog-pagination.php';
$html = ob_get_clean();
preg_match_all('/href="([^"]+)"/', $html, $matches);
check(count($matches[1]) >= 4, 'Page navigation renders links');
foreach ($matches[1] as $link) {
    parse_str((string)parse_url(html_entity_decode($link), PHP_URL_QUERY), $params);
    foreach (['q', 'card_type', 'per_page', 'pool'] as $key) {
        check(($params[$key] ?? null) === $_GET[$key], 'Pagination preserves ' . $key);
    }
}
check(str_contains($html, 'aria-current="page">2'), 'Current page is accessible');
echo "OK: shared catalogue controls and pagination\n";
