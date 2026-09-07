<?php
declare(strict_types=1);

function h($value): string { return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }
function panel_logout_csrf_token(): string { return 'test-csrf'; }
function check(bool $value, string $message): void { if (!$value) throw new RuntimeException($message); }

$action = 'parsers';
$cardType = '';
$workspaceSection = 'Операции';
$workspaceTitle = 'Источники';
$panelUser = ['login' => '<img onerror=bad()>'];
ob_start();
require __DIR__ . '/../partials/sidebar.php';
require __DIR__ . '/../partials/topbar.php';
$html = ob_get_clean();
check(substr_count($html, 'aria-current="page"') === 1, 'Exactly one current navigation destination');
check(str_contains($html, 'href="/?action=parsers" aria-current="page"'), 'Sources active destination');
foreach (['hero', 'hero_skin', 'pet', 'coin', 'timewarped', 'constructed', 'anomaly', 'quest', 'darkmoon_prize', 'reward', 'trinket'] as $type) {
    check(str_contains($html, 'href="/?card_type=' . $type . '"'), 'Catalogue destination preserved: ' . $type);
}
check(str_contains($html, 'href="/?action=api_tokens"'), 'Token manager remains reachable');
check(str_contains($html, 'href="/?action=analytics"'), 'Overview opens at the page heading');
check(!str_contains($html, 'analytics#statistics'), 'Overview navigation must not skip the page heading');
check(str_contains($html, 'action="/auth/logout" method="post"'), 'Logout stays POST');
check(str_contains($html, 'name="csrf" value="test-csrf"'), 'Logout CSRF remains present');
check(!str_contains($html, '<img onerror'), 'Login is escaped');
check(str_contains($html, '&lt;img onerror=bad()&gt;'), 'Escaped login is visible');
check(!str_contains(panel_icon('<script>'), '<script>'), 'Icon helper uses static paths');
foreach (['new', 'edit', 'wiki_terms'] as $action) {
    ob_start();
    require __DIR__ . '/../partials/sidebar.php';
    $html = ob_get_clean();
    check(!str_contains($html, 'aria-current="page"'), 'Editor is not the catalogue page: ' . $action);
    check(str_contains($html, 'class="sidebar-catalog" open'), 'Keep catalogue group available in editor');
}
echo "OK: shared panel shell\n";
