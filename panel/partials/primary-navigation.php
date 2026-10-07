<?php declare(strict_types=1); ?>
<nav class="primary-navigation" aria-label="Основные разделы">
    <a href="/"<?= ($action ?? 'list') === 'list' ? ' aria-current="page"' : '' ?>>Каталог</a>
    <a href="/?action=analytics"<?= ($action ?? '') === 'analytics' ? ' aria-current="page"' : '' ?>>Статистика</a>
    <a href="/?action=parsers"<?= ($action ?? '') === 'parsers' ? ' aria-current="page"' : '' ?>>Источники</a>
    <a href="/?action=api_tokens"<?= ($action ?? '') === 'api_tokens' ? ' aria-current="page"' : '' ?>>API-токены</a>
</nav>
