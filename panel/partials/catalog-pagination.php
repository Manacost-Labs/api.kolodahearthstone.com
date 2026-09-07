<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/catalog_navigation.php';
$pageInputId = !empty($paginationBottom) ? 'catalog-page-bottom' : 'catalog-page-top';
?>
            <nav class="pagination<?= !empty($paginationBottom) ? ' bottom' : '' ?>" aria-label="Страницы карт">
                <?php if ($page > 1): ?>
                    <a class="page-link page-first" href="<?= h(query_url(['page' => 1])) ?>">Первая</a>
                    <a class="page-link page-prev" href="<?= h(query_url(['page' => $page - 1])) ?>">Назад</a>
                <?php else: ?>
                    <span class="page-link page-first disabled">Первая</span>
                    <span class="page-link page-prev disabled">Назад</span>
                <?php endif; ?>

                <?php if ($pageWindowStart > 1): ?>
                    <a class="page-link" href="<?= h(query_url(['page' => 1])) ?>">1</a>
                    <?php if ($pageWindowStart > 2): ?><span class="page-gap">...</span><?php endif; ?>
                <?php endif; ?>

                <?php for ($i = $pageWindowStart; $i <= $pageWindowEnd; $i++): ?>
                    <?php if ($i === $page): ?>
                        <span class="page-link page-number active" aria-current="page"><?= $i ?></span>
                    <?php else: ?>
                        <a class="page-link page-number" href="<?= h(query_url(['page' => $i])) ?>"><?= $i ?></a>
                    <?php endif; ?>
                <?php endfor; ?>

                <?php if ($pageWindowEnd < $totalPages): ?>
                    <?php if ($pageWindowEnd < $totalPages - 1): ?><span class="page-gap">...</span><?php endif; ?>
                    <a class="page-link" href="<?= h(query_url(['page' => $totalPages])) ?>"><?= $totalPages ?></a>
                <?php endif; ?>

                <?php if ($page < $totalPages): ?>
                    <a class="page-link page-next" href="<?= h(query_url(['page' => $page + 1])) ?>">Вперед</a>
                    <a class="page-link page-last" href="<?= h(query_url(['page' => $totalPages])) ?>">Последняя</a>
                <?php else: ?>
                    <span class="page-link page-next disabled">Вперед</span>
                    <span class="page-link page-last disabled">Последняя</span>
                <?php endif; ?>
                <span class="page-summary">Страница <?= $page ?> из <?= $totalPages ?></span>
                <form class="catalog-page-jump" method="get" action="<?= h(parse_url(query_url(), PHP_URL_PATH) ?: '/') ?>">
                    <?php foreach (panel_catalog_page_fields($_GET) as $name => $value): ?>
                        <input type="hidden" name="<?= h($name) ?>" value="<?= h($value) ?>">
                    <?php endforeach; ?>
                    <label for="<?= $pageInputId ?>">На страницу</label>
                    <input id="<?= $pageInputId ?>" name="page" type="number" min="1" max="<?= $totalPages ?>" value="<?= $page ?>" inputmode="numeric" required>
                    <button class="button secondary" type="submit">Перейти</button>
                </form>
            </nav>
