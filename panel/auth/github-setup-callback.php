<?php
declare(strict_types=1);

require __DIR__ . '/../lib/auth.php';

panel_start_session();
panel_security_headers();
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
    header('Allow: GET');
    panel_render_auth_page('Метод не поддерживается', 'Запустите настройку заново.', '<a class="button secondary" href="/auth/github/setup">Начать заново</a>', 405);
    exit;
}

if (panel_auth_config() !== null) {
    panel_render_auth_page('GitHub-вход уже настроен', 'Можно войти в панель.', '<a class="button" href="/auth/github">Войти через GitHub</a>');
    exit;
}

if (!panel_consume_state($_SESSION, 'github_setup', $_GET['state'] ?? '', null, PANEL_AUTH_SETUP_STATE_TTL)) {
    panel_auth_audit('setup_invalid_state');
    panel_render_auth_page('Сессия настройки устарела', 'Защитный код не совпал или уже был использован.', '<a class="button" href="/auth/github/setup">Начать заново</a>', 403);
    exit;
}

try {
    $app = panel_github_app_from_manifest_code((string)($_GET['code'] ?? ''));
    if (!panel_user_is_allowed($app['owner'])) {
        panel_auth_audit('setup_denied', ['user_id' => $app['owner']['id'] ?? 0]);
        panel_render_auth_page('Неверный владелец приложения', 'Создать приложение может только аккаунт Zulut30.', '<a class="button secondary" href="/auth/github/setup">Начать заново</a>', 403);
        exit;
    }

    panel_write_auth_config($app);
    session_regenerate_id(true);
    panel_sign_in($_SESSION, $app['owner']);
    panel_auth_audit('setup_success', ['user_id' => $app['owner']['id']]);
    header('Location: /', true, 302);
    exit;
} catch (Throwable $exception) {
    panel_auth_audit('setup_failed');
    panel_render_auth_page('Настройка не завершена', 'GitHub не вернул конфигурацию или её не удалось безопасно сохранить.', '<a class="button" href="/auth/github/setup">Повторить настройку</a>', 502);
}
