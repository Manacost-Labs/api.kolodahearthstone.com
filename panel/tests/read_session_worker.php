<?php
declare(strict_types=1);

// Isolated synthetic sessions only. Never use the application's session directory.
[$script, $directory, $mode] = $argv;
ini_set('session.save_path', $directory);
ini_set('session.use_cookies', '0');
session_id('synthetic-navigation-test');
$started = microtime(true);
session_start();
if ($mode === 'probe') {
    $persisted = ($_SESSION['csrf'] ?? '') === 'synthetic-form'
        && ($_SESSION['logout_csrf'] ?? '') === 'synthetic-logout'
        && ($_SESSION['last_seen_at'] ?? 0) === 123;
    session_write_close();
    echo json_encode(['wait_ms' => (microtime(true) - $started) * 1000, 'persisted' => $persisted]);
    exit;
}
$_SESSION = ['csrf' => 'synthetic-form', 'logout_csrf' => 'synthetic-logout', 'last_seen_at' => 123];
if ($mode === 'release') {
    require __DIR__ . '/../lib/read_session.php';
    panel_finish_read_session();
    panel_finish_read_session(); // Safe if the caller has already closed it.
    if (session_status() === PHP_SESSION_ACTIVE) throw new RuntimeException('Session still locked');
}
echo "ready\n";
flush();
usleep(600000); // Simulate a slow API/SQL response after authorization.
