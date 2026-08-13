<?php
declare(strict_types=1);

require __DIR__ . '/../lib/parser_control.php';

function parser_test_assert(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

$temporaryDirectory = sys_get_temp_dir() . '/parser-panel-test-' . bin2hex(random_bytes(5));
mkdir($temporaryDirectory, 0700, true);
$configPath = $temporaryDirectory . '/parser-control.json';
putenv('PANEL_PARSER_CONTROL_TOKEN_PATH=' . $configPath);

$tokenId = str_repeat('A', 12);
$token = 'khs_v1_' . $tokenId . '_' . str_repeat('a', 48);
file_put_contents($configPath, json_encode([
    'token' => $token,
    'token_id' => $tokenId,
    'expires_at' => gmdate(DATE_ATOM, time() + 3600),
]));
chmod($configPath, 0600);
$config = panel_parser_control_config();
parser_test_assert(is_array($config), 'valid parser control config must load');
parser_test_assert($config['token_id'] === $tokenId, 'token id must be preserved');

chmod($configPath, 0644);
clearstatcache(true, $configPath);
parser_test_assert(panel_parser_control_config() === null, 'group-readable config must be rejected');
chmod($configPath, 0600);
clearstatcache(true, $configPath);

$run = panel_parser_control_run_payload([
    'source_ids' => ['hsguru-meta', 'hsguru-meta'],
    'reason' => 'Проверка нового патча',
], 'github:Zulut30');
parser_test_assert($run['sourceIds'] === ['hsguru-meta'], 'run source ids must be deduplicated');
parser_test_assert($run['requestedBy'] === 'github:Zulut30', 'run actor must be forwarded');

$section = panel_parser_control_section_payload([
    'section_id' => 'constructed',
    'revision' => '3',
    'enabled' => false,
], 'github:Zulut30');
parser_test_assert($section['expectedRevision'] === 3, 'section revision must be normalized');
parser_test_assert($section['sections'] === ['constructed' => false], 'section switch must be narrow');

$session = [];
for ($index = 0; $index < PANEL_PARSER_CONTROL_RUN_LIMIT; $index++) {
    parser_test_assert(panel_parser_control_consume_run_budget($session, 1000), 'run budget must allow bounded actions');
}
parser_test_assert(!panel_parser_control_consume_run_budget($session, 1000), 'run budget must reject excess actions');

$_SESSION = [];
$csrf = panel_parser_control_csrf_token();
panel_parser_control_require_csrf($csrf);
$csrfRejected = false;
try {
    panel_parser_control_require_csrf(str_repeat('0', 64));
} catch (RuntimeException $exception) {
    $csrfRejected = true;
}
parser_test_assert($csrfRejected, 'invalid CSRF token must be rejected');

unlink($configPath);
rmdir($temporaryDirectory);
putenv('PANEL_PARSER_CONTROL_TOKEN_PATH');

echo "OK: parser control panel helpers\n";
