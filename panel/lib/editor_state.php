<?php
declare(strict_types=1);

/** Resolve the editor view after dispatch; the injected lookup is read-only. */
function panel_editor_state($action, array $post, array $query, string $error, callable $lookup, ?array $current = null): array
{
    $state = ['action' => $action, 'card' => null, 'error' => $error];
    if ($action === 'save_wiki_terms' && $error !== '') {
        $state['action'] = 'wiki_terms';
        return $state;
    }
    $failedSave = $action === 'save' && $error !== '';
    if (!$failedSave && $action !== 'edit') return $state;

    // A rejected POST owns its identity; a stale query string must not select
    // another record or turn an edit into an insert on the next submission.
    $requestedId = $failedSave ? ($post['id'] ?? '') : ($query['id'] ?? '');
    if ($failedSave && ($requestedId === '' || $requestedId === null)) {
        $state['action'] = 'new';
        return $state;
    }
    $id = is_int($requestedId) || is_string($requestedId)
        ? filter_var($requestedId, FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]) : false;
    if ($id === false) {
        $state['action'] = 'list';
        $state['error'] = trim($error . ' Некорректный ID карты. Откройте запись из каталога.');
        return $state;
    }
    try {
        $card = $failedSave && $current !== null && (int)($current['id'] ?? 0) === $id
            ? $current : $lookup($id);
    } catch (Throwable $exception) {
        $state['action'] = 'list';
        $state['error'] = trim($error . ' Не удалось загрузить карту. Обновите страницу перед повтором.');
        return $state;
    }
    if (!$card) {
        $state['action'] = 'list';
        $state['error'] = trim($error . ' Карта не найдена. Откройте запись из каталога.');
        return $state;
    }
    $state['action'] = 'edit';
    $state['card'] = $card;
    return $state;
}

/** Restore only known translation rows after rejection, never client-added terms. */
function panel_wiki_editor_values(array $groups, array $post, string $error): array
{
    if ($error === '' || ($post['action'] ?? '') !== 'save_wiki_terms' || !is_array($post['terms'] ?? null)) return $groups;
    foreach ($groups as $type => $rows) {
        $submitted = $post['terms'][$type] ?? null;
        if (!is_array($submitted)) continue;
        $values = [];
        foreach ($submitted as $key => $payload) {
            $en = is_array($payload) ? ($payload['en'] ?? null) : $key;
            $ru = is_array($payload) ? ($payload['ru'] ?? null) : $payload;
            if (is_string($en) && is_scalar($ru)) $values[$en] = (string)$ru;
        }
        foreach ($rows as $index => $row) {
            if (array_key_exists($row['term_en'], $values)) $groups[$type][$index]['term_ru'] = $values[$row['term_en']];
        }
    }
    return $groups;
}
