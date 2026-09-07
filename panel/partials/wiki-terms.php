<?php declare(strict_types=1);
        $wikiTermStats = [];
        $wikiTermTotal = 0;
        $wikiTermMissingTotal = 0;
        foreach ($wikiTermGroups as $termType => $termRows) {
            $missing = 0;
            foreach ($termRows as $termRow) {
                if (trim((string)($termRow['term_ru'] ?? '')) === '') {
                    $missing++;
                }
            }
            $count = count($termRows);
            $wikiTermTotal += $count;
            $wikiTermMissingTotal += $missing;
            $wikiTermStats[$termType] = [
                'label' => $wikiTermLabels[$termType] ?? $termType,
                'total' => $count,
                'missing' => $missing,
                'done' => $count - $missing,
            ];
        }
        ?>
            <header class="workspace-page-head">
                <div>
                    <h1>Переводы Wiki</h1>
                    <p class="muted">Заполните русские названия для английских Wiki mechanics, Wiki tags и Full tags. API сразу отдаст mechanics/tags в локализованных полях.</p>
                </div>
                <a class="button secondary" href="<?= h(query_url(['action' => null])) ?>">Назад к базе</a>
            </header>
        <section class="panel terms-panel" data-terms-page>
            <div class="terms-summary-grid" aria-label="Покрытие переводов Wiki">
                <div class="term-stat is-total">
                    <span>Всего терминов</span>
                    <b><?= $wikiTermTotal ?></b>
                </div>
                <div class="term-stat<?= $wikiTermMissingTotal > 0 ? ' is-warn' : ' is-ok' ?>">
                    <span>Без перевода</span>
                    <b><?= $wikiTermMissingTotal ?></b>
                </div>
                <?php foreach ($wikiTermStats as $stat): ?>
                    <div class="term-stat">
                        <span><?= h($stat['label']) ?></span>
                        <b><?= $stat['done'] ?>/<?= $stat['total'] ?></b>
                    </div>
                <?php endforeach; ?>
            </div>
            <div class="terms-toolbar">
                <label class="term-search">
                    <span>Поиск термина</span>
                    <input type="search" placeholder="Battlecry, Race, BG..." data-term-filter autocomplete="off">
                </label>
                <div class="term-status-filter" aria-label="Фильтр переводов">
                    <button class="button ghost active" type="button" data-term-status="all" aria-pressed="true">Все</button>
                    <button class="button ghost" type="button" data-term-status="missing" aria-pressed="false">Без перевода</button>
                    <button class="button ghost" type="button" data-term-status="translated" aria-pressed="false">Переведённые</button>
                </div>
            </div>
            <p class="terms-count" data-term-count role="status"></p>
            <div class="terms-empty" data-term-empty hidden><h2>Термины не найдены</h2><p>Измените запрос или сбросьте фильтры.</p><button class="button secondary" type="button" data-term-reset>Сбросить фильтры</button></div>
            <form method="post" class="terms-form">
                <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
                <input type="hidden" name="action" value="save_wiki_terms">
                <div class="term-grid">
                    <?php foreach ($wikiTermLabels as $termType => $termTitle): ?>
                        <section class="term-column" data-term-section="<?= h($termType) ?>">
                            <div class="term-column-head">
                                <h2><?= h($termTitle) ?></h2>
                                <?php $stat = $wikiTermStats[$termType] ?? ['done' => 0, 'total' => 0, 'missing' => 0]; ?>
                                <span><?= (int)$stat['done'] ?>/<?= (int)$stat['total'] ?> готово<?php if ((int)$stat['missing'] > 0): ?> · <?= (int)$stat['missing'] ?> пусто<?php endif; ?></span>
                            </div>
                            <?php if (!empty($wikiTermGroups[$termType])): ?>
                                <?php foreach ($wikiTermGroups[$termType] as $termIndex => $termRow): ?>
                                    <?php
                                    $termRu = trim((string)($termRow['term_ru'] ?? ''));
                                    $termStatus = $termRu === '' ? 'missing' : 'translated';
                                    $termSearchText = mb_strtolower(($termRow['term_en'] ?? '') . ' ' . $termRu . ' ' . $termTitle, 'UTF-8');
                                    ?>
                                    <label class="term-row" data-term-row data-term-status="<?= h($termStatus) ?>" data-term-text="<?= h($termSearchText) ?>">
                                        <span class="term-source">
                                            <code><?= h($termRow['term_en']) ?></code>
                                            <small><?= h($termTitle) ?></small>
                                        </span>
                                        <input type="hidden" name="terms[<?= h($termType) ?>][<?= (int)$termIndex ?>][en]" value="<?= h($termRow['term_en']) ?>">
                                        <input
                                            name="terms[<?= h($termType) ?>][<?= (int)$termIndex ?>][ru]"
                                            value="<?= h($termRu) ?>"
                                            placeholder="Русский перевод"
                                        >
                                    </label>
                                <?php endforeach; ?>
                            <?php else: ?>
                                <p class="muted">Пока нет терминов этого типа.</p>
                            <?php endif; ?>
                        </section>
                    <?php endforeach; ?>
                </div>
                <div class="actions">
                    <button class="button" type="submit">Сохранить переводы</button>
                </div>
            </form>
        </section>
