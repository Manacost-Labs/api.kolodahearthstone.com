<?php
declare(strict_types=1);

function check_session(bool $condition, string $message): void {
    if (!$condition) throw new RuntimeException($message);
}
function session_worker(string $directory, string $mode): array {
    $process = proc_open([PHP_BINARY, __DIR__ . '/read_session_worker.php', $directory, $mode],
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    check_session(is_resource($process), 'Worker started');
    fclose($pipes[0]);
    return [$process, $pipes];
}
function finish_worker(array $worker): string {
    [$process, $pipes] = $worker;
    $output = stream_get_contents($pipes[1]);
    $error = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    check_session(proc_close($process) === 0 && $error === '', 'Worker completed without errors');
    return $output;
}
$directory = sys_get_temp_dir() . '/panel-session-test-' . bin2hex(random_bytes(8));
mkdir($directory, 0700);
try {
    $waits = [];
    foreach (['hold', 'release'] as $mode) {
        $holder = session_worker($directory, $mode);
        check_session(fgets($holder[1][1]) === "ready\n", 'Holder reached slow operation');
        $result = json_decode(finish_worker(session_worker($directory, 'probe')), true);
        finish_worker($holder);
        check_session($result['persisted'] === true, 'CSRF and last-seen writes persisted before release');
        $waits[$mode] = $result['wait_ms'];
    }
    check_session($waits['hold'] > 350, 'Baseline reproduces serialized requests');
    check_session($waits['release'] < $waits['hold'] / 2, 'Read-only requests no longer wait for slow work');
    printf("Synthetic same-session wait: held=%.1f ms, released=%.1f ms\n", $waits['hold'], $waits['release']);

    $index = file_get_contents(__DIR__ . '/../index.php');
    check_session(strpos($index, "if ((\$_SERVER['REQUEST_METHOD'] ?? 'GET') === 'GET' && \$action === 'list')") !== false,
        'Only read-only catalogue GET closes early');
    $close = strpos($index, 'panel_finish_read_session();');
    check_session(strpos($index, '$panelUser = panel_require_auth();') < $close
        && strpos($index, '$panelLogoutCsrf = panel_logout_csrf_token();') < $close
        && $close < strpos($index, '$pdo = db($config);'), 'Auth and CSRF before close; SQL after');
    $topbar = file_get_contents(__DIR__ . '/../partials/topbar.php');
    check_session(strpos($topbar, '$panelLogoutCsrf ?? panel_logout_csrf_token()') !== false, 'Topbar does not reopen closed session');
    foreach (['analytics.php', 'parser-control.php'] as $file) {
        $source = file_get_contents(__DIR__ . '/../' . $file);
        check_session(strpos($source, 'panel_require_auth(true);') < strpos($source, 'panel_finish_read_session();'), 'Auth remains required: ' . $file);
    }
    echo "OK: read session concurrency and persistence\n";
} finally {
    $fixture = $directory . '/sess_synthetic-navigation-test';
    if (is_file($fixture)) unlink($fixture);
    rmdir($directory);
}
