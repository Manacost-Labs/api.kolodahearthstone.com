<?php
declare(strict_types=1);

require __DIR__ . '/../lib/auth.php';

panel_start_session();
panel_security_headers();
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
    header('Allow: GET');
    panel_render_auth_page('Метод не поддерживается', 'Повторите вход через GitHub.', '<a class="button secondary" href="/auth/github">Повторить вход</a>', 405);
    exit;
}

if (isset($_GET['error'])) {
    panel_auth_audit('login_cancelled');
    panel_render_auth_page('Вход отменён', 'GitHub не передал подтверждение. Можно безопасно попробовать ещё раз.', '<a class="button" href="/auth/github">Повторить вход</a>', 403);
    exit;
}

$state = $_GET['state'] ?? '';
if (!panel_consume_state($_SESSION, 'github_login', $state)) {
    panel_auth_audit('login_invalid_state');
    panel_render_auth_page('Сессия входа устарела', 'Защитный код не совпал или уже был использован.', '<a class="button" href="/auth/github">Начать заново</a>', 403);
    exit;
}

try {
    $config = panel_auth_config();
    if ($config === null) {
        throw new RuntimeException('GitHub-вход не настроен.');
    }
    $user = panel_github_user_from_code((string)($_GET['code'] ?? ''), $config);
    if (!panel_user_is_allowed($user)) {
        panel_auth_audit('login_denied', ['user_id' => $user['id'] ?? 0]);
        panel_render_auth_page('Доступ запрещён', 'Эта панель доступна только аккаунту Zulut30.', '<a class="button secondary" href="/auth/github">Войти другим аккаунтом</a>', 403);
        exit;
    }

    session_regenerate_id(true);
    panel_sign_in($_SESSION, $user);
    panel_auth_audit('login_success', ['user_id' => $user['id']]);
    header('Location: ' . panel_take_return_to(), true, 302);
    exit;
} catch (Throwable $exception) {
    panel_auth_audit('login_failed');
    panel_render_auth_page('GitHub не подтвердил вход', 'Повторите попытку через несколько секунд.', '<a class="button" href="/auth/github">Повторить вход</a>', 502);
}
