<?php
declare(strict_types=1);
// Standalone UI only: a submitted form is deliberately rejected, never saved.
$initialAction = ($_GET['mode'] ?? '') === 'wiki' ? 'wiki_terms' : (($_GET['mode'] ?? '') === 'edit' ? 'edit' : 'new');
$action = $_POST['action'] ?? $initialAction;
require __DIR__ . '/../lib/editor_state.php';
require __DIR__ . '/shell_fixture.php';
function h($value): string { return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }
function csrf(): string { return 'fixture-csrf'; }
function query_url(array $overrides = []): string { return '/tests/catalog_panel_fixture.php'; }
function card_types(): array { return ['minion'=>'Существо', 'spell'=>'Заклинание']; }
function creature_types(): array { return ['murloc'=>'Мурлок', 'beast'=>'Зверь', 'dragon'=>'Дракон']; }
function versioned_asset($path, $updated = null): string { return (string)$path; }
$error = ($_SERVER['REQUEST_METHOD'] ?? '') === 'POST' ? 'Тестовый отказ сохранения. Запись и файлы не изменены.' : '';
$editorState = panel_editor_state($action, $_POST, ['id'=>$_GET['id'] ?? 42], $error,
    static function (int $id): ?array { return $id === 42 ? ['id'=>42] : null; });
$action = $editorState['action']; $editCard = $editorState['card']; $error = $editorState['error'];
$form = ['id'=>$editCard ? 42 : '', 'name'=>$editCard ? 'Мурлок-разведчик' : '', 'name_en'=>'Murloc Scout',
    'card_id'=>'BG_FIXTURE_1', 'dbf'=>'10001', 'card_type'=>'minion', 'tavern_tier'=>'1', 'creature_type'=>'murloc',
    'attack'=>'2', 'health'=>'3', 'in_pool'=>true, 'duos_only'=>false, 'notes'=>'',
    'card_image'=>'', 'golden_image'=>'', 'art_image'=>'', 'framed_image'=>''];
$wikiTermLabels = ['mechanics'=>'Механики', 'tags'=>'Теги Wiki', 'full_tags'=>'Полные теги'];
$wikiTermGroups = [
    'mechanics'=>[['term_en'=>'Battlecry', 'term_ru'=>'Боевой клич'], ['term_en'=>'Taunt', 'term_ru'=>'']],
    'tags'=>[['term_en'=>'Race', 'term_ru'=>'Тип существа'], ['term_en'=>'BG', 'term_ru'=>'']],
    'full_tags'=>[['term_en'=>'Golden', 'term_ru'=>'Золотая']],
];
if (isset($_GET['empty'])) $wikiTermGroups = [];
$wikiTermGroups = panel_wiki_editor_values($wikiTermGroups, $_POST, $error);
?>
<!doctype html>
<html lang="ru" data-theme="light"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Формы · UI fixture</title><link rel="icon" href="data:,">
    <link rel="stylesheet" href="/assets/style.css"><link rel="stylesheet" href="/assets/workspace.css">
    <script src="/assets/workspace.js" defer></script><script src="/assets/panel-ui.js" defer></script>
    <script src="/assets/editor-controls.js" defer></script>
</head><body><main class="shell">
    <?php require __DIR__ . '/../partials/sidebar.php'; ?>
    <section class="workspace" id="main-content" tabindex="-1">
        <?php require __DIR__ . '/../partials/topbar.php'; ?>
        <?php if ($error): ?><p class="notice error" role="alert"><?= h($error) ?></p><?php endif; ?>
        <?php if ($action === 'wiki_terms'): ?>
            <?php require __DIR__ . '/../partials/wiki-terms.php'; ?>
        <?php elseif (in_array($action, ['new', 'edit'], true)): ?>
            <?php require __DIR__ . '/../partials/card-editor.php'; ?>
        <?php endif; ?>
    </section>
</main><?php require __DIR__ . '/../partials/command-palette.php'; ?></body></html>
