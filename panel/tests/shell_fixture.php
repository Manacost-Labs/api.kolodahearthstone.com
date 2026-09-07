<?php
declare(strict_types=1);

// Local UI fixtures only. Production uses panel_require_auth() in index.php.
function panel_logout_csrf_token(): string { return 'fixture-logout-csrf'; }
$cardType = '';
$panelUser = ['login' => 'demo-admin'];
$workspaceSection = 'Тестовая панель';
$workspaceTitle = match ($action) {
    'analytics' => 'Обзор и статистика',
    'api_tokens' => 'Доступ к API',
    'parsers' => 'Источники данных',
    default => 'Карты Полей сражений',
};
$total = 1240;
$heroTotal = 105;
$pageFrom = 1;
$pageTo = 50;
$filteredTotal = 1240;
