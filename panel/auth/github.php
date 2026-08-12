<?php
declare(strict_types=1);

require __DIR__ . '/../lib/auth.php';

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
    header('Allow: GET');
    panel_render_auth_page('Метод не поддерживается', 'Откройте страницу входа обычной ссылкой.', '<a class="button secondary" href="/">Вернуться</a>', 405);
    exit;
}

panel_start_session();
panel_security_headers();
if (panel_authenticated_user($_SESSION) !== null) {
    header('Location: ' . panel_take_return_to(), true, 302);
    exit;
}

if (isset($_GET['return_to'])) {
    $_SESSION['panel_return_to'] = panel_safe_return_to($_GET['return_to']);
}

$config = panel_auth_config();
if ($config === null) {
    panel_render_auth_page(
        'GitHub-вход почти готов',
        'Нужно один раз зарегистрировать приватное приложение от имени Zulut30.',
        '<a class="button" href="/auth/github/setup">Настроить через GitHub</a>',
        503
    );
    exit;
}

$state = panel_issue_state($_SESSION, 'github_login');
$query = http_build_query([
    'client_id' => $config['client_id'],
    'redirect_uri' => PANEL_AUTH_CALLBACK_URL,
    'state' => $state,
    'allow_signup' => 'false',
    'prompt' => 'select_account',
], '', '&', PHP_QUERY_RFC3986);

header('Location: https://github.com/login/oauth/authorize?' . $query, true, 302);
exit;
