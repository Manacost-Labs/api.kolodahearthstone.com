<?php
declare(strict_types=1);
// Retain only editable values after a rejected save; never accept uploaded paths.
if (!empty($error) && ($_POST['action'] ?? '') === 'save') {
    foreach (['name', 'name_en', 'card_id', 'dbf', 'card_type', 'tavern_tier', 'creature_type', 'attack', 'health', 'notes'] as $field) {
        if (isset($_POST[$field]) && is_scalar($_POST[$field])) $form[$field] = (string)$_POST[$field];
    }
    $form['in_pool'] = !empty($_POST['in_pool']);
    $form['duos_only'] = !empty($_POST['duos_only']);
}
?>
        <header class="workspace-page-head">
            <div>
                <h1><?= $editCard ? 'Редактировать карту' : 'Добавить карту' ?></h1>
                <p class="muted"><?= $editCard ? 'Измените данные и сохраните запись.' : 'Заполните основные поля новой записи.' ?></p>
            </div>
            <a class="button ghost" href="/">Вернуться к таблице</a>
        </header>
    <section class="panel entry-panel<?= $editCard ? ' is-editing' : '' ?>" id="add-card">
        <form method="post" enctype="multipart/form-data" class="card-form">
            <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
            <input type="hidden" name="action" value="save">
            <input type="hidden" name="id" value="<?= h($form['id']) ?>">

            <fieldset class="editor-group"><legend>Основные данные</legend>
            <label>Название карты — обязательно
                <input name="name" value="<?= h($form['name']) ?>" required>
            </label>
            <label>Название EN
                <input name="name_en" value="<?= h($form['name_en']) ?>">
            </label>
            <label>ID карты — обязательно
                <input name="card_id" value="<?= h($form['card_id']) ?>" required placeholder="BG30_123">
            </label>
            <label>DBF ID
                <input name="dbf" type="number" min="0" value="<?= h($form['dbf']) ?>">
            </label>
            </fieldset>
            <fieldset class="editor-group"><legend>Игровые свойства</legend>
            <label>Тип карты
                <select name="card_type">
                    <?php foreach (card_types() as $value => $label): ?>
                        <option value="<?= h($value) ?>"<?= (string)($form['card_type'] ?? 'minion') === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                    <?php endforeach; ?>
                </select>
            </label>
            <label>Уровень таверны
                <input name="tavern_tier" type="number" min="1" max="7" value="<?= h($form['tavern_tier']) ?>">
            </label>
            <label>Тип существа
                <select name="creature_type">
                    <option value="">Без типа</option>
                    <?php foreach (creature_types() as $value => $label): ?>
                        <option value="<?= h($value) ?>"<?= (string)$form['creature_type'] === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                    <?php endforeach; ?>
                </select>
            </label>
            <label>Атака
                <input name="attack" type="number" value="<?= h($form['attack']) ?>">
            </label>
            <label>Здоровье
                <input name="health" type="number" value="<?= h($form['health']) ?>">
            </label>
            <label>Пул
                <span class="checkbox-line">
                    <input name="in_pool" type="checkbox" value="1"<?= !empty($form['in_pool']) ? ' checked' : '' ?>>
                    В текущем пуле
                </span>
            </label>
            <label>Дуо
                <span class="checkbox-line">
                    <input name="duos_only" type="checkbox" value="1"<?= !empty($form['duos_only']) ? ' checked' : '' ?>>
                    Только в дуо
                </span>
            </label>
            </fieldset>
            <fieldset class="editor-group"><legend>Изображения</legend>
            <p class="editor-help wide">PNG, JPEG или WebP. Новый файл заменит текущее изображение после сохранения. Пустое поле оставит его без изменений.</p>
            <label>Картинка карты
                <input name="card_image_file" type="file" accept="image/png,image/jpeg,image/webp">
            </label>
            <label>Золотая / триплет
                <input name="golden_image_file" type="file" accept="image/png,image/jpeg,image/webp">
            </label>
            <label>Арт карты
                <input name="art_image_file" type="file" accept="image/png,image/jpeg,image/webp">
            </label>
            <label>Арт в рамке
                <input name="framed_image_file" type="file" accept="image/png,image/jpeg,image/webp">
            </label>
            </fieldset>
            <label class="wide">Заметки
                <textarea name="notes" rows="3"><?= h($form['notes']) ?></textarea>
            </label>

            <div class="preview-row wide">
                <?php if (!empty($form['card_image'])): ?>
                    <figure><img src="<?= h(versioned_asset($form['card_image'], $form['updated_at'] ?? null)) ?>" alt=""><figcaption>Карта</figcaption></figure>
                <?php endif; ?>
                <?php if (!empty($form['golden_image'])): ?>
                    <figure><img src="<?= h(versioned_asset($form['golden_image'], $form['updated_at'] ?? null)) ?>" alt=""><figcaption>Золотая / триплет</figcaption></figure>
                <?php endif; ?>
                <?php if (!empty($form['art_image'])): ?>
                    <figure><img src="<?= h(versioned_asset($form['art_image'], $form['updated_at'] ?? null)) ?>" alt=""><figcaption>Арт</figcaption></figure>
                <?php endif; ?>
                <?php if (!empty($form['framed_image'])): ?>
                    <figure><img src="<?= h(versioned_asset($form['framed_image'], $form['updated_at'] ?? null)) ?>" alt=""><figcaption>Арт в рамке</figcaption></figure>
                <?php endif; ?>
            </div>

            <div class="wide actions">
                <button class="button" type="submit"><?= $editCard ? 'Сохранить' : 'Добавить' ?></button>
                <a class="button secondary" href="/">Отмена</a>
            </div>
        </form>
    </section>
