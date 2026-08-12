<?php
declare(strict_types=1);

require __DIR__ . '/../lib/auth.php';

panel_start_session();
panel_security_headers();
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'POST') {
    header('Allow: POST');
    panel_render_auth_page('Выход не выполнен', 'Для безопасного выхода используйте кнопку внутри панели.', '<a class="button secondary" href="/">Вернуться в панель</a>', 405);
    exit;
}

if (!panel_logout_csrf_is_valid($_POST['csrf'] ?? null)) {
    panel_auth_audit('logout_invalid_csrf');
    panel_render_auth_page('Сессия выхода устарела', 'Вернитесь в панель и нажмите «Выйти» ещё раз.', '<a class="button secondary" href="/">Вернуться в панель</a>', 403);
    exit;
}

panel_auth_audit('logout_success', ['user_id' => $_SESSION['panel_auth']['id'] ?? 0]);
panel_destroy_session();
header('Location: /', true, 303);
exit;
