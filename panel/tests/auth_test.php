<?php
declare(strict_types=1);

require __DIR__ . '/../lib/auth.php';

function assert_same($expected, $actual, string $message): void
{
    if ($expected !== $actual) {
        fwrite(STDERR, $message . "\nExpected: " . var_export($expected, true) . "\nActual: " . var_export($actual, true) . "\n");
        exit(1);
    }
}

assert_same(true, panel_user_is_allowed(['id' => 243011385, 'login' => 'Zulut30']), 'The configured GitHub owner must be allowed.');
assert_same(true, panel_user_is_allowed(['id' => 243011385, 'login' => 'zulut30']), 'GitHub login comparison must be case-insensitive.');
assert_same(false, panel_user_is_allowed(['id' => 243011386, 'login' => 'Zulut30']), 'A matching login with a different immutable ID must be denied.');
assert_same(false, panel_user_is_allowed(['id' => 243011385, 'login' => 'attacker']), 'A renamed or spoofed login must be denied.');

$manifest = panel_github_manifest();
assert_same(false, $manifest['public'], 'The GitHub App must be private.');
assert_same([], $manifest['default_events'], 'The sign-in app must not subscribe to events.');
assert_same(PANEL_AUTH_CALLBACK_URL, $manifest['callback_urls'][0] ?? null, 'The callback URL must be exact and fixed.');
assert_same([], (array)$manifest['default_permissions'], 'The sign-in app must not request repository permissions.');

assert_same('/?action=analytics#statistics', panel_safe_return_to('/?action=analytics#statistics'), 'A local panel URL must be preserved.');
assert_same('/index.php?page=2', panel_safe_return_to('/index.php?page=2'), 'The legacy panel entrypoint must be preserved.');
assert_same('/', panel_safe_return_to('https://evil.example/'), 'An absolute return URL must be rejected.');
assert_same('/', panel_safe_return_to('//evil.example/'), 'A scheme-relative return URL must be rejected.');
assert_same('/', panel_safe_return_to('/analytics.php'), 'A JSON endpoint must not be a post-login destination.');
assert_same('/', panel_safe_return_to("/\nevil"), 'Control characters must be rejected.');

$stateSession = [];
$state = panel_issue_state($stateSession, 'oauth', 1_000);
assert_same(false, panel_consume_state($stateSession, 'oauth', 'wrong', 1_001, 600), 'A wrong OAuth state must be rejected.');
assert_same(true, panel_consume_state($stateSession, 'oauth', $state, 1_001, 600), 'The issued OAuth state must be accepted once.');
assert_same(false, panel_consume_state($stateSession, 'oauth', $state, 1_002, 600), 'An OAuth state must be one-time use.');
$expiredState = panel_issue_state($stateSession, 'oauth', 1_000);
assert_same(false, panel_consume_state($stateSession, 'oauth', $expiredState, 1_601, 600), 'An expired OAuth state must be rejected.');

$validSession = [
    'panel_auth' => [
        'id' => 243011385,
        'login' => 'Zulut30',
        'authenticated_at' => 1_000,
        'last_seen_at' => 1_500,
    ],
];
assert_same('Zulut30', panel_authenticated_user($validSession, 1_600)['login'] ?? null, 'A valid authenticated session must be accepted.');
assert_same(1_600, $validSession['panel_auth']['last_seen_at'], 'A valid session must refresh its activity timestamp.');

$idleSession = $validSession;
$idleSession['panel_auth']['last_seen_at'] = 1_000;
assert_same(null, panel_authenticated_user($idleSession, 1_000 + PANEL_AUTH_IDLE_TTL + 1), 'An idle session must expire.');
assert_same(false, isset($idleSession['panel_auth']), 'An expired session must be removed.');

$absoluteSession = $validSession;
$absoluteSession['panel_auth']['authenticated_at'] = 1_000;
$absoluteSession['panel_auth']['last_seen_at'] = 1_000 + PANEL_AUTH_ABSOLUTE_TTL;
assert_same(null, panel_authenticated_user($absoluteSession, 1_000 + PANEL_AUTH_ABSOLUTE_TTL + 1), 'A session must have an absolute expiry.');

$spoofedSession = $validSession;
$spoofedSession['panel_auth']['id'] = 42;
assert_same(null, panel_authenticated_user($spoofedSession, 1_601), 'A session for another account must be rejected.');

$temporaryDirectory = sys_get_temp_dir() . '/koloda-auth-test-' . bin2hex(random_bytes(6));
if (!mkdir($temporaryDirectory, 0700)) {
    fwrite(STDERR, "Could not create a temporary auth test directory.\n");
    exit(1);
}
$temporaryConfig = $temporaryDirectory . '/github-auth.json';
putenv('PANEL_AUTH_CONFIG_PATH=' . $temporaryConfig);
panel_write_auth_config([
    'client_id' => 'Iv1.testclient12345',
    'client_secret' => 'test-secret-that-is-long-enough',
    'app_id' => 123,
    'app_slug' => 'test-app',
    'pem' => 'must-not-be-persisted',
    'webhook_secret' => 'must-not-be-persisted',
]);
$storedConfig = json_decode((string)file_get_contents($temporaryConfig), true);
assert_same(['client_id', 'client_secret', 'app_id', 'app_slug', 'created_at'], array_keys($storedConfig), 'Only the credentials required for login may be persisted.');
assert_same(false, isset($storedConfig['pem']), 'The unused GitHub private key must not be persisted.');
assert_same(false, isset($storedConfig['webhook_secret']), 'The unused webhook secret must not be persisted.');
clearstatcache(true, $temporaryConfig);
assert_same(0600, fileperms($temporaryConfig) & 0777, 'The credential file must be owner-readable only.');
assert_same('Iv1.testclient12345', panel_auth_config()['client_id'] ?? null, 'A valid stored auth configuration must load.');
unlink($temporaryConfig);
rmdir($temporaryDirectory);
putenv('PANEL_AUTH_CONFIG_PATH');

fwrite(STDOUT, "auth tests: ok\n");
