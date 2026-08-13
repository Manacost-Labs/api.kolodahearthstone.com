<?php
declare(strict_types=1);

require __DIR__ . '/../lib/api_tokens.php';

function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function csrf(): string
{
    return 'fixture-csrf';
}

$apiTokenManagerConfig = [
    'token_id' => 'PanelKey0001',
    'expires_at' => '2027-08-12T00:00:00Z',
];
$apiTokenIssueNonce = 'fixture-nonce';
$issuedApiToken = [
    'token' => 'khs_v1_DemoToken001_' . str_repeat('x', 43),
];
$apiTokenLoadError = '';
$apiTokens = [
    [
        'id' => 'PanelKey0001',
        'name' => 'web-panel-token-manager',
        'scopes' => ['tokens:manage'],
        'created_at' => '2026-08-12T16:00:00Z',
        'expires_at' => '2027-08-12T16:00:00Z',
        'last_used_at' => '2026-08-12T16:50:00Z',
        'revoked_at' => null,
        'rate_limit_per_minute' => 120,
        'monthly_quota' => 10000,
        'usage' => [
            'month' => '2026-08',
            'request_count' => 28,
            'error_count' => 1,
            'response_bytes' => 12000,
            'last_request_at' => '2026-08-12T16:50:00Z',
        ],
    ],
    [
        'id' => 'DemoRead0001',
        'name' => 'WordPress production',
        'scopes' => ['database:read'],
        'created_at' => '2026-08-10T12:00:00Z',
        'expires_at' => '2026-11-08T12:00:00Z',
        'last_used_at' => '2026-08-12T16:45:00Z',
        'revoked_at' => null,
        'rate_limit_per_minute' => 600,
        'monthly_quota' => 1000000,
        'usage' => [
            'month' => '2026-08',
            'request_count' => 15420,
            'error_count' => 14,
            'response_bytes' => 18200000,
            'last_request_at' => '2026-08-12T16:45:00Z',
        ],
    ],
];
?>
<!doctype html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>API-токены · UI fixture</title>
    <link rel="stylesheet" href="/assets/style.css?v=31">
    <script src="/assets/panel-ui.js?v=2" defer></script>
</head>
<body>
<main class="workspace">
    <?php require __DIR__ . '/../partials/api-token-manager.php'; ?>
</main>
</body>
</html>
