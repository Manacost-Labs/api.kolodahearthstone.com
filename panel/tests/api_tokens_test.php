<?php
declare(strict_types=1);

require __DIR__ . '/../lib/api_tokens.php';

function token_assert_same($expected, $actual, string $message): void
{
    if ($expected !== $actual) {
        fwrite(STDERR, $message . "\nExpected: " . var_export($expected, true) . "\nActual: " . var_export($actual, true) . "\n");
        exit(1);
    }
}

function token_assert_throws(callable $operation, string $message): void
{
    try {
        $operation();
    } catch (InvalidArgumentException $exception) {
        return;
    }

    fwrite(STDERR, $message . "\n");
    exit(1);
}

$payload = panel_api_token_normalize_issue_input([
    'name' => '  telegram-bot  ',
    'scopes' => ['database:read', 'admin', 'database:read'],
    'expires_in_days' => '90',
]);
token_assert_same('telegram-bot', $payload['name'], 'Token names must be trimmed.');
token_assert_same(['admin', 'database:read'], $payload['scopes'], 'Scopes must be allowlisted, unique and stable.');
token_assert_same(90, $payload['expires_in_days'], 'Expiry must be normalized to an integer.');

token_assert_throws(static function (): void {
    panel_api_token_normalize_issue_input([
        'name' => 'integration',
        'scopes' => ['root'],
        'expires_in_days' => '90',
    ]);
}, 'Unsupported scopes must be rejected.');
token_assert_throws(static function (): void {
    panel_api_token_normalize_issue_input([
        'name' => '',
        'scopes' => ['database:read'],
        'expires_in_days' => '90',
    ]);
}, 'Empty token names must be rejected.');
token_assert_throws(static function (): void {
    panel_api_token_normalize_issue_input([
        'name' => 'integration',
        'scopes' => ['database:read'],
        'expires_in_days' => '366',
    ]);
}, 'Token lifetime must be bounded.');

token_assert_same(true, panel_api_token_id_is_valid('AbCdEf12_-xy'), 'Valid token IDs must be accepted.');
token_assert_same(false, panel_api_token_id_is_valid('../bootstrap'), 'Path-like token IDs must be rejected.');

$rateSession = [];
for ($attempt = 0; $attempt < PANEL_API_TOKEN_ISSUE_LIMIT; $attempt++) {
    token_assert_same(true, panel_api_token_consume_issue_budget($rateSession, 1_000), 'Allowed issue attempts must pass.');
}
token_assert_same(false, panel_api_token_consume_issue_budget($rateSession, 1_001), 'Issue rate limit must block excess attempts.');
token_assert_same(true, panel_api_token_consume_issue_budget($rateSession, 1_000 + PANEL_API_TOKEN_ISSUE_WINDOW + 1), 'Issue budget must recover after the window.');

$temporaryDirectory = sys_get_temp_dir() . '/koloda-api-token-test-' . bin2hex(random_bytes(6));
if (!mkdir($temporaryDirectory, 0700)) {
    fwrite(STDERR, "Could not create a temporary token test directory.\n");
    exit(1);
}
$temporaryConfig = $temporaryDirectory . '/api-token-manager.json';
putenv('PANEL_API_TOKEN_CONFIG_PATH=' . $temporaryConfig);
$managerId = implode('', ['AbCdEf12', '_-xy']);
$managerToken = implode('', ['khs_', 'v1_', $managerId, '_', str_repeat('A', 43)]);
file_put_contents($temporaryConfig, json_encode([
    'token' => $managerToken,
    'token_id' => $managerId,
    'expires_at' => '2027-08-12T00:00:00Z',
]));
chmod($temporaryConfig, 0600);
$manager = panel_api_token_manager_config();
token_assert_same($managerToken, $manager['token'] ?? null, 'A valid manager credential must load.');
token_assert_same($managerId, $manager['token_id'] ?? null, 'The manager ID must be available for self-revoke protection.');
token_assert_throws(static function () use ($managerId): void {
    panel_api_token_revoke($managerId);
}, 'The panel must not revoke its own manager credential.');

file_put_contents($temporaryConfig, json_encode([
    'token' => implode('-', ['plaintext', 'invalid']),
    'token_id' => $managerId,
]));
token_assert_same(null, panel_api_token_manager_config(), 'Malformed manager credentials must fail closed.');

unlink($temporaryConfig);
rmdir($temporaryDirectory);
putenv('PANEL_API_TOKEN_CONFIG_PATH');

fwrite(STDOUT, "api token panel tests: ok\n");
