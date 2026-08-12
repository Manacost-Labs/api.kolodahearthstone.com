<?php
declare(strict_types=1);

require __DIR__ . '/../lib/auth.php';

panel_start_session();
panel_security_headers();
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
    header('Allow: GET');
    panel_render_auth_page('Метод не поддерживается', 'Откройте мастер настройки обычной ссылкой.', '', 405);
    exit;
}

if (panel_auth_config() !== null) {
    panel_render_auth_page('GitHub-вход уже настроен', 'Приложение зарегистрировано, можно войти в панель.', '<a class="button" href="/auth/github">Войти через GitHub</a>');
    exit;
}

$state = panel_issue_state($_SESSION, 'github_setup');
$manifest = json_encode(panel_github_manifest(), JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
if (!is_string($manifest)) {
    panel_render_auth_page('Настройка недоступна', 'Не удалось подготовить GitHub-приложение.', '', 500);
    exit;
}

$action = 'https://github.com/settings/apps/new?state=' . rawurlencode($state);
$form = '<form action="' . panel_html($action) . '" method="post">'
    . '<input type="hidden" name="manifest" value="' . panel_html($manifest) . '">'
    . '<button class="button" type="submit">Создать приватное GitHub App</button>'
    . '</form>';
panel_render_auth_page(
    'Одноразовая настройка',
    'GitHub покажет параметры приложения. Проверьте аккаунт Zulut30 и нажмите «Create GitHub App».',
    $form
);
