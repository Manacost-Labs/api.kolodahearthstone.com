<?php declare(strict_types=1); ?>
        <div class="cards-table<?= $showPets || $showCoins || $showHeroSkins ? ' is-gallery' : '' ?>">
            <?php if ($showConstructed): ?>
            <table class="constructed-table">
                <thead>
                <tr>
                    <th scope="col">Карта RU</th>
                    <th scope="col">Card EN</th>
                    <th scope="col">Crop</th>
                    <th scope="col">Форматы</th>
                    <th scope="col">Set</th>
                    <th scope="col">Тип</th>
                    <th scope="col">Класс</th>
                    <th scope="col">Мана</th>
                    <th scope="col">Статы</th>
                    <th scope="col">Картинки</th>
                    <th scope="col">Wiki</th>
                    <th scope="col">Gallery</th>
                    <th scope="col">Patch changes</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($constructedCards as $card): ?>
                    <?php
                    $wikiMeta = $constructedWikiMetaMap[(string)$card['card_id']] ?? null;
                    $wikiMechanics = json_array($wikiMeta['wiki_mechanics_json'] ?? null);
                    $wikiTags = json_array($wikiMeta['wiki_tags_json'] ?? null);
                    $banLists = json_array($wikiMeta['ban_lists_json'] ?? null);
                    $gallery = json_array($wikiMeta['gallery_json'] ?? null);
                    $patchChanges = json_array($wikiMeta['patch_changes_json'] ?? null);
                    $externalLinks = json_array($wikiMeta['external_links_json'] ?? null);
                    $relatedGroups = json_array($wikiMeta['related_cards_json'] ?? null);
                    $relatedCardIds = json_array($wikiMeta['related_card_ids_json'] ?? null);
                    $sounds = json_array($wikiMeta['sounds_json'] ?? null);
                    $goldenCards = json_array($wikiMeta['golden_cards_json'] ?? null);
                    $signatureCards = json_array($wikiMeta['signature_cards_json'] ?? null);
                    $diamondCards = json_array($wikiMeta['diamond_cards_json'] ?? null);
                    $diamondAnimated = json_array($wikiMeta['diamond_animated_json'] ?? null);
                    $soundCount = wiki_sound_count($wikiMeta);
                    $relatedCount = wiki_related_count($wikiMeta);
                    $cardImage = (string)($card['local_image_url'] ?: $card['image_url'] ?: $card['crop_image_url'] ?: '');
                    $cropImage = (string)($card['local_crop_image_url'] ?: $card['crop_image_url'] ?: '');
                    $goldenImage = (string)($card['local_gold_image_url'] ?: $card['image_gold_url'] ?: ($goldenCards[0]['file_url'] ?? ''));
                    $signatureImage = (string)($card['image_signature_url'] ?: ($signatureCards[0]['file_url'] ?? ''));
                    $diamondImage = (string)($card['image_diamond_url'] ?: ($diamondCards[0]['file_url'] ?? ''));
                    $animatedDiamondImage = (string)($card['animated_diamond_url'] ?: ($diamondAnimated[0]['file_url'] ?? ''));
                    $tooltip = constructed_card_tooltip($card);
                    $formatSlugs = array_filter(explode(',', (string)($card['formats'] ?? '')));
                    ?>
                    <tr data-row data-search="<?= h(constructed_card_search_text($card)) ?>">
                        <td class="card-name" title="<?= h($tooltip) ?>">
                            <?php if ($cardImage): ?>
                                <img
                                    src="<?= h($cardImage) ?>"
                                    alt="<?= h($card['name_ru'] ?: $card['name_en']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($cardImage) ?>"
                                    data-tooltip="<?= h($tooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                            <span class="card-name-copy">
                                <span><?= h($card['name_ru'] ?: '—') ?></span>
                                <a class="card-stats-link" data-stats-card-id="<?= h($card['card_id']) ?>" data-stats-dbf-id="<?= h($card['dbf']) ?>" href="/?action=analytics&amp;stats=card&amp;stats_q=<?= rawurlencode((string)($card['name_en'] ?: $card['name_ru'])) ?>#statistics">Статистика</a>
                            </span>
                        </td>
                        <td class="name-en">
                            <b><?= h($card['name_en'] ?: '—') ?></b>
                            <?php if (!empty($card['text_ru']) || !empty($card['text_en'])): ?>
                                <span class="subtext"><?= h(strip_tags((string)($card['text_ru'] ?: $card['text_en']))) ?></span>
                            <?php endif; ?>
                            <?php if (!empty($card['flavor_ru'])): ?>
                                <span class="subtext flavor-line"><?= h(strip_tags((string)$card['flavor_ru'])) ?></span>
                            <?php endif; ?>
                            <code><?= h($card['card_id']) ?></code>
                            <span class="muted-dash">dbf <?= h($card['dbf'] ?: '—') ?></span>
                        </td>
                        <td><?= horizontal_art_preview($card['horizontal_image_url'] ?? null, (string)($card['name_ru'] ?: $card['name_en'])) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td>
                            <div class="wiki-tags compact-tags">
                                <?php foreach ($formatSlugs as $formatSlug): ?>
                                    <span><?= h(constructed_format_label($formatSlug)) ?></span>
                                <?php endforeach; ?>
                            </div>
                        </td>
                        <td><?= h($card['card_set'] ?: '—') ?></td>
                        <td><span class="type-badge"><?= h(constructed_card_type_label($card['card_type'] ?? '')) ?></span></td>
                        <td><?= h($card['class_slug'] ?: '—') ?></td>
                        <td><?= h($card['mana_cost'] ?? '—') ?></td>
                        <td>
                            <?php if ($card['attack'] !== null || $card['health'] !== null): ?>
                                <?= h(($card['attack'] ?? '—') . ' / ' . ($card['health'] ?? '—')) ?>
                                <?php if (!empty($card['minion_type'])): ?><span class="muted-dash"><?= h($card['minion_type']) ?></span><?php endif; ?>
                                <?php if (!empty($card['spell_school'])): ?><span class="muted-dash"><?= h($card['spell_school']) ?></span><?php endif; ?>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td class="constructed-images">
                            <?php if ($cropImage): ?>
                                <img class="variant-preview" src="<?= h($cropImage) ?>" alt="Арт <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($cropImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nАрт") ?>">
                            <?php endif; ?>
                            <?php if ($goldenImage): ?>
                                <img class="variant-preview" src="<?= h($goldenImage) ?>" alt="Golden <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($goldenImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nGolden card") ?>">
                            <?php endif; ?>
                            <?php if ($signatureImage): ?>
                                <img class="variant-preview" src="<?= h($signatureImage) ?>" alt="Signature <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($signatureImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nSignature card") ?>">
                            <?php endif; ?>
                            <?php if ($diamondImage): ?>
                                <img class="variant-preview diamond-preview" src="<?= h($diamondImage) ?>" alt="Diamond <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($diamondImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nDiamond card") ?>">
                            <?php endif; ?>
                            <?php if ($animatedDiamondImage): ?>
                                <img class="variant-preview diamond-preview" src="<?= h($animatedDiamondImage) ?>" alt="Animated Diamond <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($animatedDiamondImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nAnimated Diamond") ?>">
                            <?php endif; ?>
                            <?php if (!$cropImage && !$goldenImage && !$signatureImage && !$diamondImage && !$animatedDiamondImage): ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="wiki-cell">
                            <?php if ($wikiMeta): ?>
                                <details class="wiki-details">
                                    <summary>
                                        <span class="wiki-status<?= h(wiki_status_class($wikiMeta)) ?>"><?= h(wiki_status_label($wikiMeta)) ?></span>
                                        <span class="wiki-brief">
                                            <?= h($card['artist'] ?: 'без художника') ?>
                                            <?php if ($gallery): ?> · <?= count($gallery) ?> арт<?php endif; ?>
                                            <?php if ($soundCount > 0): ?> · <?= $soundCount ?> зв.<?php endif; ?>
                                            <?php if ($relatedCount > 0): ?> · <?= $relatedCount ?> связ.<?php endif; ?>
                                        </span>
                                    </summary>
                                    <div class="wiki-panel">
                                        <?php if (($wikiMeta['status'] ?? '') !== 'ok'): ?>
                                            <div class="wiki-muted"><?= h($wikiMeta['error'] ?: 'Wiki-данные еще не синхронизированы.') ?></div>
                                        <?php else: ?>
                                            <div class="wiki-grid">
                                                <div><b>Artist</b><span><?= h($card['artist'] ?: '—') ?></span></div>
                                                <div><b>Rarity</b><span><?= h($card['rarity'] ?: '—') ?></span></div>
                                                <div><b>Fetched</b><span><?= h($wikiMeta['fetched_at'] ?: '—') ?></span></div>
                                                <div><b>Changed</b><span><?= h($wikiMeta['changed_at'] ?: '—') ?></span></div>
                                            </div>
                                            <?php if (!empty($wikiMeta['wiki_page_url'])): ?>
                                                <a class="wiki-link" href="<?= h($wikiMeta['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                            <?php elseif (!empty($card['wiki_page_url'])): ?>
                                                <a class="wiki-link" href="<?= h($card['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                            <?php endif; ?>
                                            <?php if ($wikiMechanics): ?>
                                                <div class="wiki-section"><b>Wiki mechanics</b><div class="wiki-tags"><?php foreach ($wikiMechanics as $item): ?><span><?= h($item) ?></span><?php endforeach; ?></div></div>
                                            <?php endif; ?>
                                            <?php if ($wikiTags): ?>
                                                <div class="wiki-section"><b>Wiki tags</b><div class="wiki-tags"><?php foreach ($wikiTags as $item): ?><span><?= h($item) ?></span><?php endforeach; ?></div></div>
                                            <?php endif; ?>
                                            <?php if ($banLists): ?>
                                                <div class="wiki-section"><b>Ban lists</b><ul class="wiki-list"><?php foreach ($banLists as $ban): ?><li><?= h($ban['text'] ?? compact_text($ban)) ?><?php if (!empty($ban['url'])): ?> <a href="<?= h($ban['url']) ?>" target="_blank" rel="noopener">link</a><?php endif; ?></li><?php endforeach; ?></ul></div>
                                            <?php endif; ?>
                                            <?php if ($relatedGroups): ?>
                                                <div class="wiki-section constructed-related-section">
                                                    <b>Сопутствующие карты</b>
                                                    <?php foreach ($relatedGroups as $group): ?>
                                                        <section class="constructed-related-group">
                                                            <h4><?= h(constructed_related_heading_ru($group['heading'] ?? null)) ?></h4>
                                                            <div class="constructed-related-grid">
                                                                <?php foreach (($group['cards'] ?? []) as $related): ?>
                                                                    <?php
                                                                    $relatedId = trim((string)($related['card_id'] ?? ''));
                                                                    $relatedCard = $relatedId !== '' ? ($constructedRelatedCardMap[$relatedId] ?? null) : null;
                                                                    $relatedImage = $relatedCard
                                                                        ? (string)($relatedCard['local_image_url'] ?: $relatedCard['image_url'] ?: '')
                                                                        : (string)($related['image_url'] ?? '');
                                                                    $relatedArt = $relatedCard
                                                                        ? (string)($relatedCard['local_wiki_full_art_url'] ?: $relatedCard['wiki_full_art_url'] ?: '')
                                                                        : '';
                                                                    $relatedNameRu = $relatedCard
                                                                        ? (string)($relatedCard['name_ru'] ?: $relatedCard['name_en'] ?: ($related['title'] ?? $relatedId))
                                                                        : (string)(($related['title'] ?? '') ?: ($relatedId ?: 'Неизвестная карта'));
                                                                    $relatedNameEn = $relatedCard ? (string)($relatedCard['name_en'] ?? '') : (string)($related['title'] ?? '');
                                                                    $relatedTooltip = $relatedCard ? constructed_card_tooltip($relatedCard) : $relatedNameRu;
                                                                    $relatedWikiUrl = (string)($related['url'] ?? '');
                                                                    ?>
                                                                    <article class="constructed-related-card">
                                                                        <?php if ($relatedImage !== ''): ?>
                                                                            <img
                                                                                src="<?= h($relatedImage) ?>"
                                                                                alt="<?= h($relatedNameRu) ?>"
                                                                                loading="lazy"
                                                                                decoding="async"
                                                                                tabindex="0"
                                                                                role="button"
                                                                                aria-label="Открыть карту <?= h($relatedNameRu) ?> на весь экран"
                                                                                data-preview="<?= h($relatedImage) ?>"
                                                                                data-tooltip="<?= h($relatedTooltip) ?>"
                                                                            >
                                                                        <?php endif; ?>
                                                                        <div class="constructed-related-copy">
                                                                            <strong><?= h($relatedNameRu) ?></strong>
                                                                            <?php if ($relatedNameEn !== '' && $relatedNameEn !== $relatedNameRu): ?>
                                                                                <span><?= h($relatedNameEn) ?></span>
                                                                            <?php endif; ?>
                                                                            <?php if ($relatedCard): ?>
                                                                                <div class="constructed-related-meta" aria-label="Характеристики карты">
                                                                                    <span><?= h(constructed_card_type_label($relatedCard['card_type'] ?? '')) ?></span>
                                                                                    <?php if ($relatedCard['mana_cost'] !== null): ?><span>Мана <?= h($relatedCard['mana_cost']) ?></span><?php endif; ?>
                                                                                    <?php if ($relatedCard['attack'] !== null || $relatedCard['health'] !== null): ?>
                                                                                        <span><?= h(($relatedCard['attack'] ?? '—') . ' / ' . ($relatedCard['health'] ?? '—')) ?></span>
                                                                                    <?php endif; ?>
                                                                                </div>
                                                                            <?php endif; ?>
                                                                            <?php if ($relatedCard && (!empty($relatedCard['text_ru']) || !empty($relatedCard['text_en']))): ?>
                                                                                <p><?= h(strip_tags((string)($relatedCard['text_ru'] ?: $relatedCard['text_en']))) ?></p>
                                                                            <?php endif; ?>
                                                                            <?php if ($relatedArt !== ''): ?>
                                                                                <figure class="constructed-related-art">
                                                                                    <img
                                                                                        src="<?= h($relatedArt) ?>"
                                                                                        alt="Оригинальный Wiki full art карты <?= h($relatedNameRu) ?>"
                                                                                        loading="lazy"
                                                                                        decoding="async"
                                                                                        tabindex="0"
                                                                                        role="button"
                                                                                        aria-label="Открыть оригинальный Wiki full art карты <?= h($relatedNameRu) ?>"
                                                                                        data-preview="<?= h($relatedArt) ?>"
                                                                                        data-tooltip="<?= h($relatedNameRu . "\nОригинальный Wiki full art" . (!empty($relatedCard['wiki_full_art_width']) && !empty($relatedCard['wiki_full_art_height']) ? "\n" . $relatedCard['wiki_full_art_width'] . "×" . $relatedCard['wiki_full_art_height'] : '') . (!empty($relatedCard['artist']) ? "\nХудожник: " . $relatedCard['artist'] : '')) ?>"
                                                                                    >
                                                                                    <figcaption>
                                                                                        <?php if (!empty($relatedCard['wiki_full_art_file_page_url'])): ?>
                                                                                            <a href="<?= h($relatedCard['wiki_full_art_file_page_url']) ?>" target="_blank" rel="noopener">Wiki full art</a>
                                                                                        <?php else: ?>
                                                                                            Wiki full art
                                                                                        <?php endif; ?>
                                                                                        <?php if (!empty($relatedCard['wiki_full_art_width']) && !empty($relatedCard['wiki_full_art_height'])): ?>
                                                                                            · <?= h($relatedCard['wiki_full_art_width']) ?>×<?= h($relatedCard['wiki_full_art_height']) ?>
                                                                                        <?php endif; ?>
                                                                                        <?php if (!empty($relatedCard['artist'])): ?> · <?= h($relatedCard['artist']) ?><?php endif; ?>
                                                                                    </figcaption>
                                                                                </figure>
                                                                            <?php elseif ($relatedCard && !empty($relatedCard['artist'])): ?>
                                                                                <span>Художник: <?= h($relatedCard['artist']) ?></span>
                                                                            <?php endif; ?>
                                                                            <div class="constructed-related-links">
                                                                                <code><?= h($relatedId !== '' ? $relatedId : 'no id') ?></code>
                                                                                <?php if ($relatedWikiUrl !== ''): ?>
                                                                                    <a href="<?= h($relatedWikiUrl) ?>" target="_blank" rel="noopener">Wiki</a>
                                                                                <?php endif; ?>
                                                                            </div>
                                                                            <?php if (!$relatedCard): ?><em>Локализация ожидает импорта</em><?php endif; ?>
                                                                        </div>
                                                                    </article>
                                                                <?php endforeach; ?>
                                                            </div>
                                                        </section>
                                                    <?php endforeach; ?>
                                                </div>
                                            <?php endif; ?>
                                            <?php if ($relatedCardIds): ?>
                                                <div class="wiki-section"><b>Related IDs</b><div class="wiki-tags"><?php foreach ($relatedCardIds as $relatedId): ?><code><?= h($relatedId) ?></code><?php endforeach; ?></div></div>
                                            <?php endif; ?>
                                            <?php if ($sounds): ?>
                                                <div class="wiki-section"><b>Sounds</b><ul class="wiki-list">
                                                    <?php foreach ($sounds as $group): ?>
                                                        <?php foreach (($group['clips'] ?? []) as $clip): ?>
                                                            <li><?= h(($group['heading'] ?? $clip['group'] ?? 'Sound') . ': ' . ($clip['description'] ?? '')) ?> <?php if (!empty($clip['file_url'])): ?><a href="<?= h($clip['file_url']) ?>" target="_blank" rel="noopener"><?= h($clip['file_title'] ?? 'audio') ?></a><?php endif; ?></li>
                                                        <?php endforeach; ?>
                                                    <?php endforeach; ?>
                                                </ul></div>
                                            <?php endif; ?>
                                            <?php if ($externalLinks): ?>
                                                <div class="wiki-section"><b>External links</b><ul class="wiki-list"><?php foreach ($externalLinks as $link): ?><li><a href="<?= h($link['url'] ?? '#') ?>" target="_blank" rel="noopener"><?= h($link['label'] ?? $link['url'] ?? 'link') ?></a></li><?php endforeach; ?></ul></div>
                                            <?php endif; ?>
                                        <?php endif; ?>
                                    </div>
                                </details>
                            <?php else: ?>
                                <span class="wiki-status empty">В очереди</span>
                            <?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($gallery): ?>
                                <details class="media-details"><summary><?= count($gallery) ?> images</summary><div class="hero-media-grid art-grid">
                                    <?php foreach ($gallery as $item): ?>
                                        <?php
                                        $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                        $galleryFull = (string)($item['file_url'] ?? $galleryImage);
                                        $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Gallery image');
                                        ?>
                                        <figure class="hero-media-item art-item">
                                            <?php if ($galleryImage !== ''): ?>
                                                <img src="<?= h($galleryImage) ?>" alt="<?= h($galleryTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($galleryFull) ?>" data-tooltip="<?= h($galleryTitle) ?>">
                                            <?php endif; ?>
                                            <figcaption><a href="<?= h($item['file_page_url'] ?? $galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a></figcaption>
                                        </figure>
                                    <?php endforeach; ?>
                                </div></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($patchChanges): ?>
                                <details><summary><?= count($patchChanges) ?> changes</summary><ul class="wiki-list">
                                    <?php foreach ($patchChanges as $changeGroup): ?>
                                        <?php foreach (($changeGroup['entries'] ?? []) as $changeEntry): ?>
                                            <li><?= h(($changeGroup['heading'] ?? 'Changes') . ': ' . ($changeEntry['date'] ?? '') . ' ' . ($changeEntry['patch'] ?? '')) ?> <?php if (!empty($changeEntry['patch_url'])): ?><a href="<?= h($changeEntry['patch_url']) ?>" target="_blank" rel="noopener">patch</a><?php endif; ?> <?php if (!empty($changeEntry['items'])): ?><span><?= h(implode(' ', array_map('strval', $changeEntry['items']))) ?></span><?php endif; ?></li>
                                        <?php endforeach; ?>
                                    <?php endforeach; ?>
                                </ul></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$constructedCards): ?>
                    <tr><td colspan="13" class="empty">Карты Standard/Wild пока не загружены.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php elseif ($showLibrary): ?>
            <table class="library-table">
                <thead>
                <tr>
                    <th scope="col">Карта RU</th>
                    <?php if ($libraryType === 'trinket'): ?><th scope="col">Full art</th><?php endif; ?>
                    <th scope="col">Crop</th>
                    <th scope="col">Описание</th>
                    <th scope="col">card_id</th>
                    <th scope="col">dbf</th>
                    <th scope="col">Статус</th>
                    <th scope="col">Тир</th>
                    <th scope="col">Группа</th>
                    <th scope="col">Тип</th>
                    <th scope="col">Источник</th>
                    <th scope="col">Wiki</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($libraryCards as $card): ?>
                    <?php
                    $cardImage = (string)($card['image_url'] ?? '');
                    $fullArtImage = (string)($card['local_full_art_url'] ?? '');
                    $tooltip = trim(implode("\n", array_filter([
                        $card['name_ru'] ?? '',
                        strip_tags((string)($card['text_ru'] ?: '')),
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <tr>
                        <td class="card-name" title="<?= h($tooltip) ?>">
                            <?php if ($cardImage): ?>
                                <img
                                    src="<?= h($cardImage) ?>"
                                    alt="<?= h($card['name_ru']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($cardImage) ?>"
                                    data-tooltip="<?= h($tooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                            <span><?= h($card['name_ru']) ?></span>
                        </td>
                        <?php if ($libraryType === 'trinket'): ?><td class="library-art-cell">
                            <?php if ($fullArtImage): ?>
                                <figure class="library-art-preview">
                                    <button
                                        type="button"
                                        class="library-art-button"
                                        data-preview="<?= h($fullArtImage) ?>"
                                        data-tooltip="<?= h('Full art · ' . $card['name_ru']) ?>"
                                        aria-label="<?= h('Открыть full art: ' . $card['name_ru']) ?>"
                                    >
                                        <img
                                            src="<?= h($fullArtImage) ?>"
                                            alt=""
                                            loading="lazy"
                                            decoding="async"
                                            width="72"
                                            height="72"
                                        >
                                    </button>
                                    <figcaption><?= h(($card['full_art_width'] ?: 512) . '×' . ($card['full_art_height'] ?: 512)) ?></figcaption>
                                </figure>
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                        </td><?php endif; ?>
                        <td><?= horizontal_art_preview($card['horizontal_image_url'] ?? null, (string)$card['name_ru']) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td class="name-en">
                            <?php if (!empty($card['text_ru'])): ?>
                                <span class="subtext"><?= h(strip_tags((string)$card['text_ru'])) ?></span>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td><code><?= h($card['card_id']) ?></code></td>
                        <td><?= h($card['dbf'] ?: '—') ?></td>
                        <td>
                            <span class="pool-badge<?= !empty($card['in_pool']) ? '' : ' off' ?>">
                                <?= !empty($card['in_pool']) ? 'В пуле' : 'Удалена' ?>
                            </span>
                        </td>
                        <td>
                            <?php if (!empty($card['tier_value'])): ?>
                                <span class="type-badge"><?= h($card['tier_name_ru'] ?: ('Тир ' . $card['tier_value'])) ?></span>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td><span class="type-badge"><?= h($card['group_name_ru'] ?: '—') ?></span></td>
                        <td><span class="type-badge"><?= h($card['card_type'] ?: '—') ?></span></td>
                        <td><?= h($card['source'] ?: '—') ?></td>
                        <td>
                            <?php if (!empty($card['wiki_page_url'])): ?>
                                <a class="wiki-link" href="<?= h($card['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$libraryCards): ?>
                    <tr><td colspan="<?= $libraryType === 'trinket' ? 12 : 11 ?>" class="empty">Записей библиотеки пока нет.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php elseif ($showTimewarped): ?>
            <table class="timewarped-table">
                <thead>
                <tr>
                    <th scope="col">Карта</th>
                    <th scope="col">Card EN</th>
                    <th scope="col">Crop</th>
                    <th scope="col">CARD_ID</th>
                    <th scope="col">DBF</th>
                    <th scope="col">Тип</th>
                    <th scope="col">Таверна</th>
                    <th scope="col">Статы</th>
                    <th scope="col">Золотая</th>
                    <th scope="col">Wiki</th>
                    <th scope="col">Gallery</th>
                    <th scope="col">Card changes</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($timewarpedCards as $card): ?>
                    <?php
                    $cardImage = (string)($card['card_image_url'] ?? '');
                    $goldenImage = (string)($card['golden_image_url'] ?? '');
                    $wikiMechanics = json_array($card['wiki_mechanics_json'] ?? null);
                    $wikiTags = json_array($card['wiki_tags_json'] ?? null);
                    $availability = json_array($card['availability_json'] ?? null);
                    $relatedGroups = json_array($card['related_cards_json'] ?? null);
                    $relatedCardIds = json_array($card['related_card_ids_json'] ?? null);
                    $sounds = json_array($card['sounds_json'] ?? null);
                    $gallery = json_array($card['gallery_json'] ?? null);
                    $cardChanges = json_array($card['card_changes_json'] ?? null);
                    $externalLinks = json_array($card['external_links_json'] ?? null);
                    $fullTags = json_array($card['full_tags_json'] ?? null);
                    $soundCount = wiki_sound_count(['sounds_json' => $card['sounds_json'] ?? null]);
                    $relatedCount = wiki_related_count(['related_cards_json' => $card['related_cards_json'] ?? null]);
                    $tooltip = trim(implode("\n", array_filter([
                        $card['name_ru'] ?? '',
                        $card['name_en'] ?? '',
                        strip_tags((string)($card['text_ru'] ?: $card['text_en'] ?: '')),
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <tr>
                        <td class="card-name" title="<?= h($tooltip) ?>">
                            <?php if ($cardImage): ?>
                                <img
                                    src="<?= h($cardImage) ?>"
                                    alt="<?= h($card['name_ru'] ?: $card['name_en']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($cardImage) ?>"
                                    data-tooltip="<?= h($tooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                            <span><?= h($card['name_ru'] ?: '—') ?></span>
                        </td>
                        <td class="name-en">
                            <b><?= h($card['name_en']) ?></b>
                            <?php if (!empty($card['text_ru']) || !empty($card['text_en'])): ?>
                                <span class="subtext"><?= h(strip_tags((string)($card['text_ru'] ?: $card['text_en']))) ?></span>
                            <?php endif; ?>
                        </td>
                        <td><?= horizontal_art_preview($card['horizontal_image_url'] ?? null, (string)($card['name_ru'] ?: $card['name_en'])) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td><code><?= h($card['card_id']) ?></code></td>
                        <td><?= h($card['dbf']) ?></td>
                        <td><span class="type-badge <?= h($card['card_type'] ?? '') ?>"><?= h(timewarped_type_label($card['card_type'] ?? '')) ?></span></td>
                        <td><?= h($card['tavern_tier'] ?: '—') ?></td>
                        <td>
                            <?php if ($card['attack'] !== null || $card['health'] !== null): ?>
                                <?= h(($card['attack'] ?? '—') . ' / ' . ($card['health'] ?? '—')) ?>
                                <?php if (!empty($card['minion_type'])): ?><span class="muted-dash"><?= h($card['minion_type']) ?></span><?php endif; ?>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if ($goldenImage): ?>
                                <img
                                    class="variant-preview"
                                    src="<?= h($goldenImage) ?>"
                                    alt="Золотая версия <?= h($card['name_en']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($goldenImage) ?>"
                                    data-tooltip="<?= h(($card['golden_name_ru'] ?: $card['golden_name_en'] ?: $card['name_en']) . "\n" . strip_tags((string)($card['golden_text_ru'] ?: $card['golden_text_en'] ?: ''))) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-mini">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td class="wiki-cell">
                            <details class="wiki-details">
                                <summary>
                                    <span class="wiki-status<?= h(wiki_status_class($card)) ?>"><?= h(wiki_status_label($card)) ?></span>
                                    <span class="wiki-brief">
                                        <?= h($card['artist'] ?: 'без художника') ?>
                                        <?php if ($soundCount > 0): ?> · <?= $soundCount ?> зв.<?php endif; ?>
                                        <?php if ($relatedCount > 0): ?> · <?= $relatedCount ?> связ.<?php endif; ?>
                                    </span>
                                </summary>
                                <div class="wiki-panel">
                                    <div class="wiki-grid">
                                        <div><b>Artist</b><span><?= h($card['artist'] ?: '—') ?></span></div>
                                        <div><b>Race</b><span><?= h($card['race'] ?: '—') ?></span></div>
                                        <div><b>Minion type</b><span><?= h($card['minion_type'] ?: '—') ?></span></div>
                                        <div><b>Fetched</b><span><?= h($card['fetched_at'] ?: '—') ?></span></div>
                                    </div>
                                    <?php if (!empty($card['wiki_page_url'])): ?>
                                        <a class="wiki-link" href="<?= h($card['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                    <?php endif; ?>
                                    <?php if ($wikiMechanics): ?>
                                        <div class="wiki-section"><b>Wiki mechanics</b><div class="wiki-tags"><?php foreach ($wikiMechanics as $item): ?><span><?= h($item) ?></span><?php endforeach; ?></div></div>
                                    <?php endif; ?>
                                    <?php if ($wikiTags): ?>
                                        <div class="wiki-section"><b>Wiki tags</b><div class="wiki-tags"><?php foreach ($wikiTags as $item): ?><span><?= h($item) ?></span><?php endforeach; ?></div></div>
                                    <?php endif; ?>
                                    <?php if (!empty($availability['notes'])): ?>
                                        <div class="wiki-section"><b>Availability</b><ul class="wiki-list"><?php foreach ($availability['notes'] as $note): ?><li><?= h($note) ?></li><?php endforeach; ?></ul></div>
                                    <?php endif; ?>
                                    <?php if ($relatedGroups): ?>
                                        <div class="wiki-section"><b>Related cards</b><ul class="wiki-list">
                                            <?php foreach ($relatedGroups as $group): ?>
                                                <?php foreach (($group['cards'] ?? []) as $related): ?>
                                                    <li><?= h($group['heading'] ?? 'Related') ?>: <code><?= h(($related['card_id'] ?? '') ?: 'no id') ?></code> <?= h($related['title'] ?? '') ?></li>
                                                <?php endforeach; ?>
                                            <?php endforeach; ?>
                                        </ul></div>
                                    <?php endif; ?>
                                    <?php if ($relatedCardIds): ?>
                                        <div class="wiki-section"><b>Related IDs</b><div class="wiki-tags"><?php foreach ($relatedCardIds as $relatedId): ?><code><?= h($relatedId) ?></code><?php endforeach; ?></div></div>
                                    <?php endif; ?>
                                    <?php if ($sounds): ?>
                                        <div class="wiki-section"><b>Sounds</b><ul class="wiki-list">
                                            <?php foreach ($sounds as $group): ?>
                                                <?php foreach (($group['clips'] ?? []) as $clip): ?>
                                                    <li><?= h(($group['heading'] ?? $clip['group'] ?? 'Sound') . ': ' . ($clip['description'] ?? '')) ?> <?php if (!empty($clip['file_url'])): ?><a href="<?= h($clip['file_url']) ?>" target="_blank" rel="noopener"><?= h($clip['file_title'] ?? 'audio') ?></a><?php endif; ?></li>
                                                <?php endforeach; ?>
                                            <?php endforeach; ?>
                                        </ul></div>
                                    <?php endif; ?>
                                    <?php if ($externalLinks): ?>
                                        <div class="wiki-section"><b>External links</b><ul class="wiki-list"><?php foreach ($externalLinks as $link): ?><li><a href="<?= h($link['url'] ?? '#') ?>" target="_blank" rel="noopener"><?= h($link['label'] ?? $link['url'] ?? 'link') ?></a></li><?php endforeach; ?></ul></div>
                                    <?php endif; ?>
                                    <?php if ($fullTags): ?>
                                        <div class="wiki-section"><b>Full tags</b><div class="wiki-tags"><?php foreach ($fullTags as $tag): ?><code><?= h($tag) ?></code><?php endforeach; ?></div></div>
                                    <?php endif; ?>
                                </div>
                            </details>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($gallery): ?>
                                <details class="media-details"><summary><?= count($gallery) ?> images</summary><div class="hero-media-grid art-grid">
                                    <?php foreach ($gallery as $item): ?>
                                        <?php
                                        $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                        $galleryFull = (string)($item['file_url'] ?? $galleryImage);
                                        $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Gallery image');
                                        ?>
                                        <figure class="hero-media-item art-item">
                                            <?php if ($galleryImage !== ''): ?>
                                                <img src="<?= h($galleryImage) ?>" alt="<?= h($galleryTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($galleryFull) ?>" data-tooltip="<?= h($galleryTitle) ?>">
                                            <?php endif; ?>
                                            <figcaption><a href="<?= h($item['file_page_url'] ?? $galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a></figcaption>
                                        </figure>
                                    <?php endforeach; ?>
                                </div></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($cardChanges): ?>
                                <details><summary><?= count($cardChanges) ?> changes</summary><ul class="wiki-list">
                                    <?php foreach ($cardChanges as $changeGroup): ?>
                                        <?php foreach (($changeGroup['entries'] ?? []) as $changeEntry): ?>
                                            <li><?= h(($changeGroup['heading'] ?? 'Changes') . ': ' . ($changeEntry['date'] ?? '') . ' ' . ($changeEntry['patch'] ?? '')) ?> <?php if (!empty($changeEntry['items'])): ?><span><?= h(implode(' ', array_map('strval', $changeEntry['items']))) ?></span><?php endif; ?></li>
                                        <?php endforeach; ?>
                                    <?php endforeach; ?>
                                </ul></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$timewarpedCards): ?>
                    <tr><td colspan="12" class="empty">Хрономальные карты пока не загружены.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php elseif ($showPets): ?>
            <div class="skin-gallery-grid">
                <?php foreach ($pets as $pet): ?>
                    <?php
                    $petGallery = json_array($pet['gallery_json'] ?? null);
                    $petCardImage = (string)($pet['card_image_url'] ?? '');
                    $petBackground = (string)($pet['end_screen_background_url'] ?? '');
                    $petTooltip = trim(implode("\n", array_filter([
                        $pet['variant_name'] ?? '',
                        $pet['pet_name'] ? 'Pet: ' . $pet['pet_name'] : '',
                        $pet['level'] ? 'Level: ' . $pet['level'] : '',
                        $pet['dbf'] ? 'DBF: ' . $pet['dbf'] : '',
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <article class="skin-card pet-card">
                        <div class="skin-card-media">
                            <?php if ($petCardImage !== ''): ?>
                                <img class="skin-portrait pet-portrait-preview" src="<?= h($petCardImage) ?>" alt="<?= h($pet['variant_name']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($petCardImage) ?>" data-tooltip="<?= h($petTooltip) ?>">
                            <?php elseif ($petBackground !== ''): ?>
                                <img class="skin-portrait pet-background-preview" src="<?= h($petBackground) ?>" alt="<?= h($pet['variant_name']) ?> end screen background" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($petBackground) ?>" data-tooltip="<?= h($petTooltip) ?>">
                            <?php else: ?>
                                <span class="missing-card-image">Нет изображения</span>
                            <?php endif; ?>
                            <div class="skin-media-strip">
                                <?php if ($petCardImage !== ''): ?>
                                    <button type="button" data-preview="<?= h($petCardImage) ?>" data-tooltip="Pet card">Карта</button>
                                <?php endif; ?>
                                <?php if ($petBackground !== ''): ?>
                                    <button type="button" data-preview="<?= h($petBackground) ?>" data-tooltip="End screen background">Фон</button>
                                <?php endif; ?>
                            </div>
                            <?= horizontal_art_preview($pet['horizontal_image_url'] ?? null, (string)$pet['variant_name']) ?>
                        </div>
                        <div class="skin-card-body">
                            <div class="skin-card-head">
                                <div>
                                    <h3><?= h($pet['variant_name']) ?></h3>
                                    <p><?= h($pet['pet_name']) ?></p>
                                </div>
                                <span class="pool-badge">Ур. <?= h($pet['level'] ?? '—') ?></span>
                            </div>
                            <div class="skin-category-row">
                                <span><?= h($pet['card_id'] ?: '—') ?></span>
                                <code><?= h($pet['dbf'] ?? '—') ?></code>
                            </div>
                            <div class="skin-meta-grid">
                                <div><b>Дата выхода</b><span><?= h(format_release_date_ru($pet['release_date'] ?? null)) ?></span></div>
                                <div><b>ID питомца</b><span><?= h($pet['pet_id']) ?></span></div>
                                <div><b>Вариант</b><span><?= h($pet['variant_id']) ?></span></div>
                                <div><b>Фон</b><span><?= $petBackground !== '' ? 'Есть' : '—' ?></span></div>
                                <div><b>Wiki</b><span><a href="<?= h($pet['page_url']) ?>" target="_blank" rel="noopener">Открыть</a></span></div>
                            </div>
                            <div class="skin-card-details">
                                <?php if ($petGallery): ?>
                                    <details class="media-details"><summary>Галерея · <?= count($petGallery) ?></summary><div class="hero-media-grid art-grid skin-gallery-mini">
                                        <?php foreach ($petGallery as $item): ?>
                                            <?php
                                            $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                            $galleryFull = (string)($item['file_url'] ?? $galleryImage);
                                            $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Gallery image');
                                            ?>
                                            <figure class="hero-media-item art-item">
                                                <?php if ($galleryImage !== ''): ?>
                                                    <img src="<?= h($galleryImage) ?>" alt="<?= h($galleryTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($galleryFull) ?>" data-tooltip="<?= h($galleryTitle) ?>">
                                                <?php endif; ?>
                                                <figcaption><a href="<?= h($galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a></figcaption>
                                            </figure>
                                        <?php endforeach; ?>
                                    </div></details>
                                <?php endif; ?>
                            </div>
                        </div>
                    </article>
                <?php endforeach; ?>
                <?php if (!$pets): ?>
                    <div class="empty">Питомцы пока не загружены.</div>
                <?php endif; ?>
            </div>
            <?php elseif ($showCoins): ?>
            <div class="skin-gallery-grid">
                <?php foreach ($coins as $coin): ?>
                    <?php
                    $coinImage = (string)($coin['image_url'] ?: $coin['wiki_image_url'] ?: '');
                    $coinCrop = (string)($coin['crop_image_url'] ?? '');
                    $coinGeneratedBy = json_array($coin['generated_by_cards_json'] ?? null);
                    $coinRelated = json_array($coin['related_cards_json'] ?? null);
                    $coinText = trim(strip_tags((string)($coin['text_ru'] ?: $coin['text_en'] ?: '')));
                    $coinTooltip = trim(implode("\n", array_filter([
                        $coin['coin_name_en'] ?? '',
                        $coin['card_name_ru'] ?? '',
                        $coinText,
                        $coin['artist'] ? 'Artist: ' . $coin['artist'] : '',
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <article class="skin-card pet-card">
                        <div class="skin-card-media">
                            <?php if ($coinImage !== ''): ?>
                                <img class="skin-portrait" src="<?= h($coinImage) ?>" alt="<?= h($coin['coin_name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($coinImage) ?>" data-tooltip="<?= h($coinTooltip) ?>">
                            <?php else: ?>
                                <span class="missing-card-image">Нет изображения</span>
                            <?php endif; ?>
                            <div class="skin-media-strip">
                                <?php if ($coinImage !== ''): ?>
                                    <button type="button" data-preview="<?= h($coinImage) ?>" data-tooltip="Coin card">Карта</button>
                                <?php endif; ?>
                                <?php if ($coinCrop !== ''): ?>
                                    <button type="button" data-preview="<?= h($coinCrop) ?>" data-tooltip="Crop art">Фрагмент</button>
                                <?php endif; ?>
                            </div>
                            <?= horizontal_art_preview($coin['horizontal_image_url'] ?? null, (string)$coin['coin_name_en']) ?>
                        </div>
                        <div class="skin-card-body">
                            <div class="skin-card-head">
                                <div>
                                    <h3><?= h($coin['coin_name_en']) ?></h3>
                                    <p><?= h($coin['card_name_ru'] ?: $coin['card_name_en'] ?: 'The Coin') ?></p>
                                </div>
                                <span class="pool-badge">Coin</span>
                            </div>
                            <?php if ($coinText !== ''): ?>
                                <p class="subtext"><?= h($coinText) ?></p>
                            <?php endif; ?>
                            <div class="skin-category-row">
                                <span><?= h($coin['artist'] ?: 'Artist unknown') ?></span>
                                <code><?= h($coin['card_id']) ?></code>
                            </div>
                            <div class="skin-meta-grid">
                                <div><b>Дата выхода</b><span><?= h(format_release_date_ru($coin['release_date'] ?? null)) ?></span></div>
                                <div><b>DBF</b><span><?= h($coin['dbf'] ?? '—') ?></span></div>
                                <div><b>Sort</b><span><?= h($coin['cosmetic_sort_order'] ?? '—') ?></span></div>
                                <div><b>Generated by</b><span><?= count($coinGeneratedBy) ?></span></div>
                                <div><b>Related with</b><span><?= count($coinRelated) ?></span></div>
                            </div>
                            <?php if (!empty($coin['flavor_text'])): ?>
                                <p class="subtext"><?= h(strip_tags((string)$coin['flavor_text'])) ?></p>
                            <?php endif; ?>
                            <div class="skin-card-details">
                                <?php if ($coinGeneratedBy): ?>
                                    <details class="media-details"><summary>Создаётся · <?= count($coinGeneratedBy) ?></summary><div class="wiki-tags skin-tags">
                                        <?php foreach ($coinGeneratedBy as $linkedCard): ?>
                                            <code title="<?= h((string)($linkedCard['name_en'] ?? $linkedCard['page_title'] ?? '')) ?>"><?= h((string)($linkedCard['card_id'] ?? '')) ?></code>
                                        <?php endforeach; ?>
                                    </div></details>
                                <?php endif; ?>
                                <?php if ($coinRelated): ?>
                                    <details class="media-details"><summary>Связанные карты · <?= count($coinRelated) ?></summary><div class="wiki-tags skin-tags">
                                        <?php foreach ($coinRelated as $linkedCard): ?>
                                            <code title="<?= h((string)($linkedCard['name_en'] ?? $linkedCard['page_title'] ?? '')) ?>"><?= h((string)($linkedCard['card_id'] ?? '')) ?></code>
                                        <?php endforeach; ?>
                                    </div></details>
                                <?php endif; ?>
                                <?php if (!empty($coin['wiki_page_url'])): ?>
                                    <a class="wiki-link" href="<?= h($coin['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                <?php endif; ?>
                            </div>
                        </div>
                    </article>
                <?php endforeach; ?>
                <?php if (!$coins): ?>
                    <div class="empty">Монетки пока не загружены.</div>
                <?php endif; ?>
            </div>
            <?php elseif ($showHeroSkins): ?>
            <div class="skin-gallery-grid">
                <?php foreach ($heroSkins as $skin): ?>
                    <?php
                    $skinCategories = json_array($skin['categories_json'] ?? null);
                    $skinTags = json_array($skin['tags_json'] ?? null);
                    $skinGallery = json_array($skin['gallery_json'] ?? null);
                    $skinSounds = json_array($skin['sounds_json'] ?? null);
                    $skinAnimatedAssets = json_array($skin['animated_asset_json'] ?? null);
                    $skinAnimatedPrimary = (string)($skin['animated_image_url'] ?: $skin['static_image_url'] ?: '');
                    $skinAnimatedType = preg_match('~\.(webm|mp4)(?:\?|$)~i', (string)$skin['animated_image_url']) ? 'video' : 'image';
                    $skinAnimatedLabel = $skinAnimatedType === 'video' ? 'WEBM' : 'GIF';
                    $skinFullArt = (string)($skin['full_art_url'] ?? '');
                    $skinRarityLabel = (string)($skin['rarity_name_ru'] ?: $skin['rarity_name_en'] ?: ($skinRarityLabels[$skin['rarity_slug'] ?? ''] ?? 'Не указана'));
                    $skinPrimaryCategory = (string)($skin['primary_category_ru'] ?: $skin['primary_category_en'] ?: '—');
                    $skinTooltip = trim(implode("\n", array_filter([
                        $skin['name_en'] ?? '',
                        $skin['character_name'] ? 'Character: ' . $skin['character_name'] : '',
                        $skinRarityLabel ? 'Rarity: ' . $skinRarityLabel : '',
                        $skin['actor'] ? 'Actor: ' . $skin['actor'] : '',
                        $skin['artist'] ? 'Artist: ' . $skin['artist'] : '',
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <article class="skin-card">
                        <div class="skin-card-media">
                            <?php if (!empty($skin['static_image_url'])): ?>
                                <img class="skin-portrait" src="<?= h($skin['static_image_url']) ?>" alt="<?= h($skin['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($skin['static_image_url']) ?>" data-tooltip="<?= h($skinTooltip) ?>">
                            <?php else: ?>
                                <span class="missing-card-image">Нет изображения</span>
                            <?php endif; ?>
                            <div class="skin-media-strip">
                                <?php if (!empty($skin['static_image_url'])): ?>
                                    <button type="button" data-preview="<?= h($skin['static_image_url']) ?>" data-tooltip="Static">Карта</button>
                                <?php endif; ?>
                                <?php if (!empty($skin['animated_image_url'])): ?>
                                    <button type="button" data-preview="<?= h($skin['animated_image_url']) ?>" data-preview-type="<?= h($skinAnimatedType) ?>" data-tooltip="Animated <?= h($skinAnimatedLabel) ?>"><?= h($skinAnimatedLabel) ?></button>
                                <?php elseif ($skinAnimatedAssets): ?>
                                    <span class="skin-asset-pill" title="<?= h(compact_text($skinAnimatedAssets)) ?>">Asset</span>
                                <?php endif; ?>
                                <?php if ($skinFullArt !== ''): ?>
                                    <button type="button" data-preview="<?= h($skinFullArt) ?>" data-tooltip="Full art">Арт</button>
                                <?php endif; ?>
                            </div>
                            <?= horizontal_art_preview($skin['horizontal_image_url'] ?? null, (string)$skin['name_en']) ?>
                        </div>
                        <div class="skin-card-body">
                            <div class="skin-card-head">
                                <div>
                                    <h3><?= h($skin['name_en']) ?></h3>
                                    <p><?= h($skin['character_name'] ?: 'Character unknown') ?></p>
                                </div>
                                <span class="pool-badge"><?= h($skin['class_name_ru'] ?: $skin['class_name_en'] ?: '—') ?></span>
                            </div>
                            <div class="skin-category-row">
                                <span><?= h($skinRarityLabel) ?> · <?= h($skinPrimaryCategory) ?></span>
                                <code><?= h($skin['card_id']) ?></code>
                            </div>
                            <div class="skin-meta-grid">
                                <div><b>Дата выхода</b><span><?= h(format_release_date_ru($skin['release_date'] ?? null)) ?></span></div>
                                <div><b>Редкость</b><span><?= h($skinRarityLabel) ?></span></div>
                                <div><b>Озвучка</b><span><?= h($skin['actor'] ?: '—') ?></span></div>
                                <div><b>Художник</b><span><?= h($skin['artist'] ?: '—') ?></span></div>
                                <div><b>DBF</b><span><?= h($skin['dbf'] ?? '—') ?></span></div>
                                <div><b>Wiki</b><span><a href="<?= h($skin['page_url']) ?>" target="_blank" rel="noopener">Открыть</a></span></div>
                            </div>
                            <?php if ($skinTags || count($skinCategories) > 1): ?>
                                <div class="wiki-tags skin-tags">
                                    <?php foreach (array_slice($skinTags, 0, 4) as $tag): ?><code><?= h($tag) ?></code><?php endforeach; ?>
                                    <?php foreach (array_slice($skinCategories, 0, 3) as $category): ?><code><?= h($category['name_ru'] ?? $category['name_en'] ?? $category['slug'] ?? '') ?></code><?php endforeach; ?>
                                </div>
                            <?php endif; ?>
                            <div class="skin-card-details">
                                <?php if ($skinGallery): ?>
                                    <details class="media-details"><summary>Галерея · <?= count($skinGallery) ?></summary><div class="hero-media-grid art-grid skin-gallery-mini">
                                        <?php foreach ($skinGallery as $item): ?>
                                            <?php
                                            $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                            $galleryFull = (string)($item['file_url'] ?? $galleryImage);
                                            $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Gallery image');
                                            ?>
                                            <figure class="hero-media-item art-item">
                                                <?php if ($galleryImage !== ''): ?>
                                                    <img src="<?= h($galleryImage) ?>" alt="<?= h($galleryTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($galleryFull) ?>" data-tooltip="<?= h($galleryTitle) ?>">
                                                <?php endif; ?>
                                                <figcaption><a href="<?= h($galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a></figcaption>
                                            </figure>
                                        <?php endforeach; ?>
                                    </div></details>
                                <?php endif; ?>
                                <?php if ($skinSounds): ?>
                                    <details class="related-media-details">
                                        <summary>Звуки · <?= count($skinSounds) ?></summary>
                                        <ul class="sound-list skin-sound-list">
                                            <?php foreach (array_slice($skinSounds, 0, 12) as $sound): ?>
                                                <li>
                                                    <span><?= h(($sound['type'] ?? 'Sound') . ': ' . ($sound['transcript'] ?? '')) ?></span>
                                                    <?php if (!empty($sound['file_url'])): ?>
                                                        <audio controls preload="none" src="<?= h($sound['file_url']) ?>"></audio>
                                                    <?php endif; ?>
                                                </li>
                                            <?php endforeach; ?>
                                        </ul>
                                    </details>
                                <?php endif; ?>
                                <?php if ($skinAnimatedAssets): ?>
                                    <details class="related-media-details">
                                        <summary>Файлы анимации · <?= count($skinAnimatedAssets) ?></summary>
                                        <div class="wiki-tags skin-tags">
                                            <?php foreach ($skinAnimatedAssets as $asset): ?><code><?= h(($asset['kind'] ?? 'asset') . ': ' . ($asset['asset'] ?? '')) ?></code><?php endforeach; ?>
                                        </div>
                                    </details>
                                <?php endif; ?>
                            </div>
                        </div>
                    </article>
                <?php endforeach; ?>
                <?php if (!$heroSkins): ?>
                    <div class="empty">Скины героев пока не загружены.</div>
                <?php endif; ?>
            </div>
            <?php elseif ($showHeroes): ?>
            <table class="heroes-table">
                <thead>
                <tr>
                    <th scope="col">Герой</th>
                    <th scope="col">Crop</th>
                    <th scope="col">RU</th>
                    <th scope="col">card_id</th>
                    <th scope="col">dbf</th>
                    <th scope="col">Armor</th>
                    <th scope="col">Сила героя</th>
                    <th scope="col">Buddy</th>
                    <th scope="col">Wiki</th>
                    <th scope="col">Hero skins</th>
                    <th scope="col">Gallery</th>
                    <th scope="col">Card changes</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($heroes as $hero): ?>
                    <?php
                    $heroPower = json_array($hero['hero_power_json'] ?? null);
                    $buddy = json_array($hero['buddy_json'] ?? null);
                    $skins = json_array($hero['hero_skins_json'] ?? null);
                    $gallery = json_array($hero['gallery_json'] ?? null);
                    $cardChanges = json_array($hero['card_changes_json'] ?? null);
                    $availability = json_array($hero['availability_json'] ?? null);
                    $externalLinks = json_array($hero['external_links_json'] ?? null);
                    $heroPowerImage = (string)($heroPower['image'] ?? $heroPower['crop_image'] ?? '');
                    $buddyImage = (string)($buddy['image'] ?? $buddy['crop_image'] ?? '');
                    $buddyGolden = is_array($buddy['golden'] ?? null) ? $buddy['golden'] : null;
                    $buddyGoldenImage = (string)($buddyGolden['image'] ?? $buddy['image_gold'] ?? '');
                    $heroPowerGallery = is_array($heroPower['gallery'] ?? null) ? $heroPower['gallery'] : [];
                    $heroPowerGalleryCount = card_gallery_count($heroPower);
                    $heroPowerSoundCount = card_sound_count($heroPower);
                    $heroPowerFullArt = (string)($heroPower['full_art_url'] ?? '');
                    $heroPowerWiki = is_array($heroPower['wiki'] ?? null) ? $heroPower['wiki'] : [];
                    $heroPowerPreview = is_array($heroPowerGallery[0] ?? null) ? $heroPowerGallery[0] : [];
                    $heroPowerPreviewImage = (string)($heroPowerPreview['thumb_url'] ?? $heroPowerPreview['file_url'] ?? $heroPowerFullArt);
                    $heroPowerPreviewFull = (string)($heroPowerPreview['file_url'] ?? $heroPowerFullArt ?? $heroPowerPreviewImage);
                    $heroPowerPreviewTitle = (string)($heroPowerPreview['caption'] ?? $heroPowerPreview['file_title'] ?? ($heroPower['name'] ?? 'Hero power art'));
                    $buddyGallery = is_array($buddy['gallery'] ?? null) ? $buddy['gallery'] : [];
                    $buddySounds = is_array($buddy['sounds'] ?? null) ? $buddy['sounds'] : [];
                    $buddyGalleryCount = card_gallery_count($buddy);
                    $buddySoundCount = card_sound_count($buddy);
                    $buddyFullArt = (string)($buddy['full_art_url'] ?? '');
                    $buddyWiki = is_array($buddy['wiki'] ?? null) ? $buddy['wiki'] : [];
                    $buddyPreview = is_array($buddyGallery[0] ?? null) ? $buddyGallery[0] : [];
                    $buddyPreviewImage = (string)($buddyPreview['thumb_url'] ?? $buddyPreview['file_url'] ?? $buddyFullArt);
                    $buddyPreviewFull = (string)($buddyPreview['file_url'] ?? $buddyFullArt ?? $buddyPreviewImage);
                    $buddyPreviewTitle = (string)($buddyPreview['caption'] ?? $buddyPreview['file_title'] ?? ($buddy['name'] ?? 'Buddy art'));
                    $heroTooltip = trim(implode("\n", array_filter([
                        $hero['name_en'] ?? '',
                        $hero['name_ru'] ?? '',
                        $hero['armor_text'] ?? '',
                        $hero['as_hero'] ?? '',
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <tr>
                        <td class="hero-card-cell">
                            <?php if (!empty($hero['hero_image_url'])): ?>
                                <img
                                    src="<?= h($hero['hero_image_url']) ?>"
                                    alt="<?= h($hero['name_en']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="64"
                                    height="92"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($hero['hero_image_url']) ?>"
                                    data-tooltip="<?= h($heroTooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                            <span class="card-name-copy">
                                <span><?= h($hero['name_en']) ?></span>
                                <a class="card-stats-link" data-stats-card-id="<?= h($hero['card_id']) ?>" data-stats-dbf-id="<?= h($hero['dbf']) ?>" href="/?action=analytics&amp;stats=card&amp;stats_q=<?= rawurlencode((string)$hero['name_en']) ?>#statistics">Статистика</a>
                            </span>
                        </td>
                        <td><?= horizontal_art_preview($hero['horizontal_image_url'] ?? null, (string)($hero['name_ru'] ?: $hero['name_en'])) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td><?= h($hero['name_ru'] ?: '—') ?></td>
                        <td><code><?= h($hero['card_id']) ?></code></td>
                        <td><?= h($hero['dbf']) ?></td>
                        <td>
                            <span class="pool-badge"><?= h($hero['armor_text'] ?: $hero['armor'] ?: '—') ?></span>
                            <?php if ($hero['duos_armor'] !== null): ?><span class="muted-dash">duos <?= h($hero['duos_armor']) ?></span><?php endif; ?>
                        </td>
                        <td class="hero-power-cell">
                            <?php if ($heroPower): ?>
                                <?php if ($heroPowerImage !== ''): ?>
                                    <img class="hero-mini-image" src="<?= h($heroPowerImage) ?>" alt="<?= h($heroPower['name'] ?? 'Hero power') ?>" loading="lazy" decoding="async">
                                <?php endif; ?>
                                <div class="related-card-block">
                                    <b><?= h($heroPower['name'] ?? '—') ?></b>
                                    <span><?= h(strip_tags((string)($heroPower['text'] ?? ''))) ?></span>
                                    <div class="related-media-badges">
                                        <?php if ($heroPowerGalleryCount > 0): ?><span><?= $heroPowerGalleryCount ?> арт</span><?php endif; ?>
                                        <?php if ($heroPowerSoundCount > 0): ?><span><?= $heroPowerSoundCount ?> зв.</span><?php endif; ?>
                                        <?php if (!empty($heroPowerWiki['page_url'])): ?><a href="<?= h($heroPowerWiki['page_url']) ?>" target="_blank" rel="noopener">wiki</a><?php endif; ?>
                                        <?php if ($heroPowerFullArt !== ''): ?><a href="<?= h($heroPowerFullArt) ?>" target="_blank" rel="noopener">full art</a><?php endif; ?>
                                    </div>
                                    <?php if ($heroPowerPreviewImage !== ''): ?>
                                        <figure class="related-art-preview">
                                            <img src="<?= h($heroPowerPreviewImage) ?>" alt="<?= h($heroPowerPreviewTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($heroPowerPreviewFull) ?>" data-tooltip="<?= h($heroPowerPreviewTitle) ?>">
                                            <figcaption><?= h($heroPowerPreviewTitle) ?></figcaption>
                                        </figure>
                                    <?php endif; ?>
                                    <?php if ($heroPowerGallery): ?>
                                        <details class="related-media-details">
                                            <summary>Все арты силы героя</summary>
                                            <div class="hero-media-grid related-art-grid">
                                                <?php foreach ($heroPowerGallery as $item): ?>
                                                    <?php
                                                    $itemImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                                    $itemFull = (string)($item['file_url'] ?? $itemImage);
                                                    $itemTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Hero power art');
                                                    ?>
                                                    <figure class="hero-media-item art-item">
                                                        <?php if ($itemImage !== ''): ?>
                                                            <img src="<?= h($itemImage) ?>" alt="<?= h($itemTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($itemFull) ?>" data-tooltip="<?= h($itemTitle) ?>">
                                                        <?php endif; ?>
                                                        <figcaption><a href="<?= h($item['file_page_url'] ?? $itemFull) ?>" target="_blank" rel="noopener"><?= h($itemTitle) ?></a></figcaption>
                                                    </figure>
                                                <?php endforeach; ?>
                                            </div>
                                        </details>
                                    <?php endif; ?>
                                </div>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td class="hero-power-cell">
                            <?php if ($buddy): ?>
                                <div class="buddy-versions">
                                    <div class="buddy-version">
                                        <?php if ($buddyImage !== ''): ?>
                                            <img class="hero-mini-image" src="<?= h($buddyImage) ?>" alt="<?= h($buddy['name'] ?? 'Buddy') ?>" loading="lazy" decoding="async">
                                        <?php endif; ?>
                                        <div class="related-card-block">
                                            <b><?= h($buddy['name'] ?? '—') ?></b>
                                            <span><?= h(strip_tags((string)($buddy['text'] ?? ''))) ?></span>
                                            <div class="related-media-badges">
                                                <?php if ($buddyGalleryCount > 0): ?><span><?= $buddyGalleryCount ?> арт</span><?php endif; ?>
                                                <?php if ($buddySoundCount > 0): ?><span><?= $buddySoundCount ?> зв.</span><?php endif; ?>
                                                <?php if (!empty($buddyWiki['page_url'])): ?><a href="<?= h($buddyWiki['page_url']) ?>" target="_blank" rel="noopener">wiki</a><?php endif; ?>
                                                <?php if ($buddyFullArt !== ''): ?><a href="<?= h($buddyFullArt) ?>" target="_blank" rel="noopener">full art</a><?php endif; ?>
                                            </div>
                                            <?php if ($buddyPreviewImage !== ''): ?>
                                                <figure class="related-art-preview">
                                                    <img src="<?= h($buddyPreviewImage) ?>" alt="<?= h($buddyPreviewTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($buddyPreviewFull) ?>" data-tooltip="<?= h($buddyPreviewTitle) ?>">
                                                    <figcaption><?= h($buddyPreviewTitle) ?></figcaption>
                                                </figure>
                                            <?php endif; ?>
                                            <?php if ($buddyGallery): ?>
                                                <details class="related-media-details">
                                                    <summary>Все арты компаньона</summary>
                                                    <div class="hero-media-grid related-art-grid">
                                                        <?php foreach ($buddyGallery as $item): ?>
                                                            <?php
                                                            $itemImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                                            $itemFull = (string)($item['file_url'] ?? $itemImage);
                                                            $itemTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Buddy art');
                                                            ?>
                                                            <figure class="hero-media-item art-item">
                                                                <?php if ($itemImage !== ''): ?>
                                                                    <img src="<?= h($itemImage) ?>" alt="<?= h($itemTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($itemFull) ?>" data-tooltip="<?= h($itemTitle) ?>">
                                                                <?php endif; ?>
                                                                <figcaption><a href="<?= h($item['file_page_url'] ?? $itemFull) ?>" target="_blank" rel="noopener"><?= h($itemTitle) ?></a></figcaption>
                                                            </figure>
                                                        <?php endforeach; ?>
                                                    </div>
                                                </details>
                                            <?php endif; ?>
                                            <?php if ($buddySounds): ?>
                                                <details class="related-media-details">
                                                    <summary>Sounds компаньона</summary>
                                                    <ul class="sound-list">
                                                        <?php foreach ($buddySounds as $soundGroup): ?>
                                                            <?php foreach (($soundGroup['clips'] ?? []) as $clip): ?>
                                                                <li>
                                                                    <span><?= h(($soundGroup['heading'] ?? $clip['group'] ?? 'Sound') . ': ' . ($clip['description'] ?? '')) ?></span>
                                                                    <?php if (!empty($clip['file_url'])): ?>
                                                                        <audio controls preload="none" src="<?= h($clip['file_url']) ?>"></audio>
                                                                    <?php endif; ?>
                                                                </li>
                                                            <?php endforeach; ?>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </details>
                                            <?php endif; ?>
                                        </div>
                                    </div>
                                    <?php if ($buddyGolden || $buddyGoldenImage !== ''): ?>
                                        <div class="buddy-version buddy-version-golden">
                                            <?php if ($buddyGoldenImage !== ''): ?>
                                                <img class="hero-mini-image" src="<?= h($buddyGoldenImage) ?>" alt="<?= h($buddyGolden['name'] ?? $buddy['name'] ?? 'Золотой buddy') ?>" loading="lazy" decoding="async">
                                            <?php endif; ?>
                                            <div>
                                                <b>Золотая версия: <?= h($buddyGolden['name'] ?? $buddy['name'] ?? '—') ?></b>
                                                <?php if (!empty($buddyGolden['text'])): ?>
                                                    <span><?= h(strip_tags((string)$buddyGolden['text'])) ?></span>
                                                <?php endif; ?>
                                            </div>
                                        </div>
                                    <?php endif; ?>
                                </div>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td class="wiki-cell">
                            <details class="wiki-details">
                                <summary>
                                    <span class="wiki-status<?= h(wiki_status_class($hero)) ?>"><?= h(wiki_status_label($hero)) ?></span>
                                    <?php if (!empty($hero['artist'])): ?><span class="wiki-brief"><?= h($hero['artist']) ?></span><?php endif; ?>
                                </summary>
                                <div class="wiki-panel">
                                    <div class="wiki-grid">
                                        <div><b>Artist</b><span><?= h($hero['artist'] ?: '—') ?></span></div>
                                        <div><b>Character</b><span><?= h($hero['character_name'] ?: '—') ?></span></div>
                                        <div><b>Race</b><span><?= h($hero['race'] ?: '—') ?></span></div>
                                        <div><b>Fetched</b><span><?= h($hero['fetched_at'] ?: '—') ?></span></div>
                                    </div>
                                    <?php if (!empty($hero['wiki_page_url'])): ?>
                                        <a class="wiki-link" href="<?= h($hero['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                    <?php endif; ?>
                                    <?php if (!empty($hero['hero_full_art_url'])): ?>
                                        <a class="wiki-link" href="<?= h($hero['hero_full_art_url']) ?>" target="_blank" rel="noopener">Hero art</a>
                                    <?php endif; ?>
                                    <?php if (!empty($hero['as_hero'])): ?>
                                        <div class="wiki-section"><b>As a hero</b><span><?= h($hero['as_hero']) ?></span></div>
                                    <?php endif; ?>
                                    <?php if (!empty($availability['notes'])): ?>
                                        <div class="wiki-section">
                                            <b>Availability</b>
                                            <ul class="wiki-list">
                                                <?php foreach ($availability['notes'] as $note): ?><li><?= h($note) ?></li><?php endforeach; ?>
                                            </ul>
                                        </div>
                                    <?php endif; ?>
                                    <?php if ($externalLinks): ?>
                                        <div class="wiki-section">
                                            <b>External links</b>
                                            <ul class="wiki-list">
                                                <?php foreach ($externalLinks as $link): ?>
                                                    <li><a href="<?= h($link['url'] ?? '#') ?>" target="_blank" rel="noopener"><?= h($link['label'] ?? $link['url'] ?? 'link') ?></a></li>
                                                <?php endforeach; ?>
                                            </ul>
                                        </div>
                                    <?php endif; ?>
                                </div>
                            </details>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($skins): ?>
                                <?php
                                $skinCards = [];
                                foreach ($skins as $skinGroup) {
                                    foreach (($skinGroup['cards'] ?? []) as $skinCard) {
                                        if (is_array($skinCard)) {
                                            $skinCards[] = $skinCard;
                                        }
                                    }
                                }
                                ?>
                                <?php if ($skinCards): ?>
                                    <details class="media-details">
                                        <summary><?= count($skinCards) ?> skins</summary>
                                        <div class="hero-media-grid">
                                            <?php foreach ($skinCards as $skinCard): ?>
                                                <?php
                                                $skinImage = (string)($skinCard['image_url'] ?? '');
                                                $skinTitle = (string)($skinCard['title'] ?? $skinCard['card_id'] ?? 'Hero skin');
                                                $skinTooltip = trim($skinTitle . "\n" . (string)($skinCard['card_id'] ?? ''));
                                                ?>
                                                <figure class="hero-media-item">
                                                    <?php if ($skinImage !== ''): ?>
                                                        <img
                                                            src="<?= h($skinImage) ?>"
                                                            alt="<?= h($skinTitle) ?>"
                                                            loading="lazy"
                                                            decoding="async"
                                                            tabindex="0"
                                                            role="button"
                                                            data-preview="<?= h($skinImage) ?>"
                                                            data-tooltip="<?= h($skinTooltip) ?>"
                                                        >
                                                    <?php endif; ?>
                                                    <figcaption>
                                                        <?php if (!empty($skinCard['url'])): ?>
                                                            <a href="<?= h($skinCard['url']) ?>" target="_blank" rel="noopener"><?= h(preg_replace('~^Battlegrounds/~', '', $skinTitle)) ?></a>
                                                        <?php else: ?>
                                                            <?= h(preg_replace('~^Battlegrounds/~', '', $skinTitle)) ?>
                                                        <?php endif; ?>
                                                    </figcaption>
                                                </figure>
                                            <?php endforeach; ?>
                                        </div>
                                    </details>
                                <?php else: ?>
                                    <span class="muted-dash">—</span>
                                <?php endif; ?>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($gallery): ?>
                                <details class="media-details"><summary><?= count($gallery) ?> images</summary><div class="hero-media-grid art-grid">
                                    <?php foreach ($gallery as $item): ?>
                                        <?php
                                        $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? $item['url'] ?? '');
                                        $galleryFull = (string)($item['file_url'] ?? $item['url'] ?? $galleryImage);
                                        $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? $item['label'] ?? $item['title'] ?? 'Gallery image');
                                        ?>
                                        <figure class="hero-media-item art-item">
                                            <?php if ($galleryImage !== ''): ?>
                                                <img
                                                    src="<?= h($galleryImage) ?>"
                                                    alt="<?= h($galleryTitle) ?>"
                                                    loading="lazy"
                                                    decoding="async"
                                                    tabindex="0"
                                                    role="button"
                                                    data-preview="<?= h($galleryFull) ?>"
                                                    data-tooltip="<?= h($galleryTitle) ?>"
                                                >
                                            <?php endif; ?>
                                            <figcaption>
                                                <?php if (!empty($item['file_page_url'])): ?>
                                                    <a href="<?= h($item['file_page_url']) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a>
                                                <?php elseif ($galleryFull !== ''): ?>
                                                    <a href="<?= h($galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a>
                                                <?php else: ?>
                                                    <?= h($galleryTitle) ?>
                                                <?php endif; ?>
                                            </figcaption>
                                        </figure>
                                    <?php endforeach; ?>
                                </div></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($cardChanges): ?>
                                <details><summary><?= count($cardChanges) ?> changes</summary><ul class="wiki-list">
                                    <?php foreach ($cardChanges as $change): ?><li><?= h(compact_text($change)) ?></li><?php endforeach; ?>
                                </ul></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$heroes): ?>
                    <tr><td colspan="12" class="empty">Герои пока не загружены.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php else: ?>
            <table class="battlegrounds-table">
                <thead>
                <tr>
                    <th scope="col">Карта</th>
                    <th scope="col">Card EN</th>
                    <th scope="col">Crop</th>
                    <th scope="col">CARD_ID</th>
                    <th scope="col">DBF</th>
                    <th scope="col">Категория</th>
                    <th scope="col">Таверна</th>
                    <th scope="col">Тип</th>
                    <th scope="col">Атака</th>
                    <th scope="col">Здоровье</th>
                    <th scope="col">В пуле</th>
                    <th scope="col">Дуо</th>
                    <th scope="col">Механики</th>
                    <th scope="col">Золотая</th>
                    <th scope="col">Арт</th>
                    <th scope="col">Рамка</th>
                    <th scope="col">Wiki</th>
                    <th scope="col">Действия</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($cards as $card): ?>
                    <?php
                    $tooltip = card_tooltip($card);
                    $cardDbf = $card['dbf'] !== null ? (int)$card['dbf'] : 0;
                    $goldenVariant = $goldenVariantMap[$cardDbf] ?? null;
                    // A golden row is a visual variant of the base card, so its own
                    // technical pool flag must not read as a gameplay restriction.
                    $goldenTooltip = $goldenVariant
                        ? str_replace(' · Не в пуле', '', card_tooltip($goldenVariant))
                        : $tooltip;
                    $cardImage = !empty($card['card_image']) ? versioned_asset($card['card_image'], $card['updated_at']) : '';
                    $goldenImage = $goldenVariant && !empty($goldenVariant['card_image'])
                        ? versioned_asset($goldenVariant['card_image'], $goldenVariant['updated_at'])
                        : (!empty($card['golden_image']) ? versioned_asset($card['golden_image'], $card['updated_at']) : '');
                    $artImage = !empty($card['art_image']) ? versioned_asset($card['art_image'], $card['updated_at']) : '';
                    $framedImage = !empty($card['framed_image']) ? versioned_asset($card['framed_image'], $card['updated_at']) : '';
                    $mechanics = card_mechanics($card['notes'] ?? null);
                    $wikiMeta = $wikiMetaMap[(string)$card['card_id']] ?? null;
                    $wikiMechanics = json_array($wikiMeta['wiki_mechanics_json'] ?? null);
                    $wikiTags = json_array($wikiMeta['wiki_tags_json'] ?? null);
                    $wikiAvailability = json_array($wikiMeta['availability_json'] ?? null);
                    $wikiSounds = json_array($wikiMeta['sounds_json'] ?? null);
                    $wikiExternalLinks = json_array($wikiMeta['external_links_json'] ?? null);
                    $wikiRelatedGroups = json_array($wikiMeta['related_cards_json'] ?? null);
                    $wikiRelatedCardIds = json_array($wikiMeta['related_card_ids_json'] ?? null);
                    $wikiCardChanges = json_array($wikiMeta['card_changes_json'] ?? null);
                    $wikiSoundCount = wiki_sound_count($wikiMeta);
                    $wikiRelatedCount = wiki_related_count($wikiMeta);
                    ?>
                    <tr
                        data-row
                        data-search="<?= h(card_search_text($card)) ?>"
                        data-tier="<?= h($card['tavern_tier']) ?>"
                        data-type="<?= h($card['creature_type']) ?>"
                        data-pool="<?= !empty($card['in_pool']) ? '1' : '0' ?>"
                        data-duos="<?= !empty($card['duos_only']) ? '1' : '0' ?>"
                    >
                        <td class="card-name" title="<?= h($tooltip) ?>">
                            <?php if ($cardImage): ?>
                                <img
                                    src="<?= h($cardImage) ?>"
                                    alt="<?= h($card['name']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    aria-label="Открыть карту <?= h($card['name']) ?> на весь экран"
                                    data-preview="<?= h($cardImage) ?>"
                                    data-tooltip="<?= h($tooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image" title="Нет рендера карты">Нет</span>
                            <?php endif; ?>
                            <span class="card-name-copy">
                                <span><?= h($card['name']) ?></span>
                                <a class="card-stats-link" data-stats-card-id="<?= h($card['card_id']) ?>" data-stats-dbf-id="<?= h($card['dbf']) ?>" href="/?action=analytics&amp;stats=card&amp;stats_q=<?= rawurlencode((string)($card['name_en'] ?: $card['name'])) ?>#statistics">Статистика</a>
                            </span>
                        </td>
                        <td class="name-en"><?= h($card['name_en'] ?: '—') ?></td>
                        <td><?= horizontal_art_preview($card['horizontal_image_url'] ?? null, (string)$card['name']) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td><code><?= h($card['card_id']) ?></code></td>
                        <td><?= h($card['dbf']) ?></td>
                        <td><span class="type-badge <?= h($card['card_type'] ?? 'minion') ?>"><?= h(card_type_label($card['card_type'] ?? 'minion')) ?></span></td>
                        <td><?= h($card['tavern_tier']) ?></td>
                        <td><?= h(creature_type_label($card['creature_type'])) ?></td>
                        <td><?= h($card['attack']) ?></td>
                        <td><?= h($card['health']) ?></td>
                        <td>
                            <span class="pool-badge<?= !empty($card['in_pool']) ? '' : ' off' ?>">
                                <?= !empty($card['in_pool']) ? 'Да' : 'Нет' ?>
                            </span>
                        </td>
                        <td>
                            <span class="pool-badge<?= !empty($card['duos_only']) ? ' duos' : ' off' ?>">
                                <?= !empty($card['duos_only']) ? 'Да' : 'Нет' ?>
                            </span>
                        </td>
                        <td class="mechanics-cell">
                            <?php if ($mechanics): ?>
                                <?php foreach ($mechanics as $mechanic): ?>
                                    <span class="mechanic-badge" title="<?= h($mechanic['slug']) ?>"><?= h($mechanic['label']) ?></span>
                                <?php endforeach; ?>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if ($goldenImage): ?>
                                <img
                                    class="variant-preview"
                                    src="<?= h($goldenImage) ?>"
                                    alt="Золотая версия <?= h($card['name']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    aria-label="Открыть золотую версию <?= h($card['name']) ?> на весь экран"
                                    data-preview="<?= h($goldenImage) ?>"
                                    data-tooltip="<?= h($goldenTooltip . "\nЗолотая / триплет") ?>"
                                >
                            <?php else: ?>
                                <span class="missing-mini">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if ($artImage): ?>
                                <img
                                    class="art-preview variant-preview"
                                    src="<?= h($artImage) ?>"
                                    alt="Арт <?= h($card['name']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="72"
                                    height="48"
                                    tabindex="0"
                                    role="button"
                                    aria-label="Открыть арт <?= h($card['name']) ?> на весь экран"
                                    data-preview="<?= h($artImage) ?>"
                                    data-tooltip="<?= h($tooltip . "\nАрт без рамки") ?>"
                                >
                            <?php else: ?>
                                <span class="missing-mini">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if ($framedImage): ?>
                                <img
                                    class="framed-preview"
                                    src="<?= h($framedImage) ?>"
                                    alt="Арт в рамке <?= h($card['name']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="48"
                                    height="56"
                                    tabindex="0"
                                    role="button"
                                    aria-label="Открыть арт в рамке <?= h($card['name']) ?> на весь экран"
                                    data-preview="<?= h($framedImage) ?>"
                                    data-tooltip="<?= h($tooltip . "\nАрт в рамке") ?>"
                                >
                            <?php else: ?>
                                <span class="missing-framed">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td class="wiki-cell">
                            <?php if ($wikiMeta): ?>
                                <details class="wiki-details">
                                    <summary>
                                        <span class="wiki-status<?= h(wiki_status_class($wikiMeta)) ?>"><?= h(wiki_status_label($wikiMeta)) ?></span>
                                        <?php if (($wikiMeta['status'] ?? '') === 'ok'): ?>
                                            <span class="wiki-brief">
                                                <?= h($wikiMeta['artist'] ?: 'без художника') ?>
                                                <?php if (!empty($wikiMeta['race'])): ?> · <?= h($wikiMeta['race']) ?><?php endif; ?>
                                                <?php if ($wikiSoundCount > 0): ?> · <?= $wikiSoundCount ?> зв.<?php endif; ?>
                                                <?php if ($wikiRelatedCount > 0): ?> · <?= $wikiRelatedCount ?> связ.<?php endif; ?>
                                            </span>
                                        <?php endif; ?>
                                    </summary>
                                    <div class="wiki-panel">
                                        <?php if (($wikiMeta['status'] ?? '') !== 'ok'): ?>
                                            <div class="wiki-muted"><?= h($wikiMeta['error'] ?: 'Нет данных wiki для этой карты.') ?></div>
                                        <?php else: ?>
                                            <div class="wiki-grid">
                                                <div><b>Artist</b><span><?= h($wikiMeta['artist'] ?: '—') ?></span></div>
                                                <div><b>Race</b><span><?= h($wikiMeta['race'] ?: '—') ?></span></div>
                                                <div><b>Minion type</b><span><?= h($wikiMeta['minion_type'] ?: '—') ?></span></div>
                                                <div><b>Fetched</b><span><?= h($wikiMeta['fetched_at'] ?: '—') ?></span></div>
                                            </div>

                                            <?php if (!empty($wikiMeta['wiki_page_url'])): ?>
                                                <a class="wiki-link" href="<?= h($wikiMeta['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                            <?php endif; ?>

                                            <?php if ($wikiMechanics): ?>
                                                <div class="wiki-section">
                                                    <b>Wiki mechanics</b>
                                                    <div class="wiki-tags">
                                                        <?php foreach ($wikiMechanics as $item): ?><span><?= h($item) ?></span><?php endforeach; ?>
                                                    </div>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiTags): ?>
                                                <div class="wiki-section">
                                                    <b>Wiki tags</b>
                                                    <div class="wiki-tags">
                                                        <?php foreach ($wikiTags as $item): ?><span><?= h($item) ?></span><?php endforeach; ?>
                                                    </div>
                                                </div>
                                            <?php endif; ?>

                                            <?php if (!empty($wikiAvailability['notes'])): ?>
                                                <div class="wiki-section">
                                                    <b>Availability</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiAvailability['notes'] as $note): ?><li><?= h($note) ?></li><?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiSounds): ?>
                                                <div class="wiki-section">
                                                    <b>Sounds</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiSounds as $group): ?>
                                                            <?php foreach (($group['clips'] ?? []) as $clip): ?>
                                                                <li>
                                                                    <span><?= h(($group['heading'] ?? $clip['group'] ?? 'Sound') . ': ' . ($clip['description'] ?? '')) ?></span>
                                                                    <?php if (!empty($clip['file_url'])): ?>
                                                                        <a href="<?= h($clip['file_url']) ?>" target="_blank" rel="noopener"><?= h($clip['file_title'] ?? 'audio') ?></a>
                                                                    <?php endif; ?>
                                                                </li>
                                                            <?php endforeach; ?>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiExternalLinks): ?>
                                                <div class="wiki-section">
                                                    <b>External links</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiExternalLinks as $link): ?>
                                                            <li><a href="<?= h($link['url'] ?? '#') ?>" target="_blank" rel="noopener"><?= h($link['label'] ?? $link['url'] ?? 'link') ?></a></li>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiRelatedGroups): ?>
                                                <div class="wiki-section">
                                                    <b>Related with</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiRelatedGroups as $group): ?>
                                                            <?php foreach (($group['cards'] ?? []) as $related): ?>
                                                                <li>
                                                                    <?= h($group['heading'] ?? 'Related') ?>:
                                                                    <code><?= h(($related['card_id'] ?? '') ?: 'no id') ?></code>
                                                                    <?= h($related['title'] ?? '') ?>
                                                                </li>
                                                            <?php endforeach; ?>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiRelatedCardIds): ?>
                                                <div class="wiki-section">
                                                    <b>Related IDs</b>
                                                    <div class="wiki-tags">
                                                        <?php foreach ($wikiRelatedCardIds as $relatedId): ?><code><?= h($relatedId) ?></code><?php endforeach; ?>
                                                    </div>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiCardChanges): ?>
                                                <div class="wiki-section">
                                                    <b>Card changes</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiCardChanges as $changeGroup): ?>
                                                            <?php foreach (($changeGroup['entries'] ?? []) as $changeEntry): ?>
                                                                <li>
                                                                    <?= h(($changeGroup['heading'] ?? 'Changes') . ': ' . ($changeEntry['date'] ?? '') . ' ' . ($changeEntry['patch'] ?? '')) ?>
                                                                    <?php if (!empty($changeEntry['patch_url'])): ?><a href="<?= h($changeEntry['patch_url']) ?>" target="_blank" rel="noopener">patch</a><?php endif; ?>
                                                                    <?php if (!empty($changeEntry['items'])): ?>
                                                                        <span><?= h(implode(' ', array_map('strval', $changeEntry['items']))) ?></span>
                                                                    <?php endif; ?>
                                                                </li>
                                                            <?php endforeach; ?>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>
                                        <?php endif; ?>
                                    </div>
                                </details>
                            <?php else: ?>
                                <span class="wiki-status empty">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td class="row-actions">
                            <a class="mini" href="/?action=edit&id=<?= (int)$card['id'] ?>">Править</a>
                            <form method="post" onsubmit="return confirm('Удалить карту?')">
                                <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
                                <input type="hidden" name="action" value="delete">
                                <input type="hidden" name="id" value="<?= (int)$card['id'] ?>">
                                <button class="mini danger" type="submit">Удалить</button>
                            </form>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$cards): ?>
                    <tr><td colspan="18" class="empty">Карт пока нет.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php endif; ?>
        </div>
