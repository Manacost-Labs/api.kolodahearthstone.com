<?php
declare(strict_types=1);
require __DIR__ . '/../lib/auth.php';
// Render only: no session, config load, OAuth request or credential mutation.
$mode = $_GET['mode'] ?? 'login';
$states = [
    'login'=>['Войти в панель', 'Используйте разрешённый GitHub-аккаунт для управления данными.', '<a class="button" href="/auth/github">Войти через GitHub</a>', 200],
    'setup'=>['Одноразовая настройка', 'Зарегистрируйте приватное GitHub App для входа в панель.', '<form action="https://github.com/settings/apps/new?state=fixture" method="post"><input type="hidden" name="manifest" value="fixture-only"><button class="button" type="submit">Создать приватное GitHub App</button></form>', 200],
    'denied'=>['Доступ запрещён', 'Этот аккаунт не имеет доступа к панели.', '<a class="button secondary" href="/auth/github">Войти другим аккаунтом</a>', 403],
    'expired'=>['Сессия входа устарела', 'Защитный код не совпал или уже был использован.', '<a class="button" href="/auth/github">Начать заново</a>', 403],
    'unavailable'=>['Настройка недоступна', 'Не удалось подготовить GitHub-приложение.', '', 500],
    'logout'=>['Сессия выхода устарела', 'Вернитесь в панель и нажмите «Выйти» ещё раз.', '<a class="button secondary" href="/">Вернуться в панель</a>', 403],
    'escape'=>['<img src=x onerror=alert(1)>', '<script>alert(2)</script>', '', 502],
];
panel_render_auth_page(...($states[$mode] ?? $states['login']));
