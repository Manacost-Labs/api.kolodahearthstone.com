<?php
declare(strict_types=1);
require __DIR__ . '/../lib/editor_state.php';
function check_editor(bool $ok, string $message): void { if (!$ok) throw new RuntimeException($message); }
$calls = [];
$lookup = static function (int $id) use (&$calls): ?array {
    $calls[] = $id;
    return $id === 42 ? ['id'=>42, 'name'=>'Original', 'card_image'=>'/existing-card.webp'] : null;
};
$state = panel_editor_state('save', ['id'=>'42'], ['id'=>99], 'Validation failed', $lookup);
check_editor($state['action'] === 'edit', 'A rejected save must reopen edit, not remain in save');
check_editor($state['card']['id'] === 42 && $calls === [42], 'Posted ID wins over a stale query ID');
check_editor($state['error'] === 'Validation failed', 'Original error remains visible');
check_editor($state['card']['card_image'] === '/existing-card.webp', 'Existing file paths are retained');
$calls = [];
$state = panel_editor_state('save', ['id'=>''], ['id'=>42], 'Validation failed', $lookup);
check_editor($state['action'] === 'new' && $state['card'] === null && !$calls, 'New-card errors never load an edit record');
$state = panel_editor_state('list', ['id'=>'42', 'action'=>'save'], [], '', $lookup);
check_editor($state['action'] === 'list' && !$calls, 'Successful dispatch stays in the catalogue');
foreach (['abc', '-1', '0', '42x', ['42'], '9999999999999999999999999'] as $invalidId) {
    $state = panel_editor_state('save', ['id'=>$invalidId], [], 'Save failed', $lookup);
    check_editor($state['action'] === 'list' && $state['card'] === null && !$calls, 'Malformed edit IDs never become inserts or lookups');
}
$state = panel_editor_state('save', ['id'=>'404'], [], 'Save failed', $lookup);
check_editor($state['action'] === 'list' && $state['card'] === null, 'A missing edit record never becomes a new card');
check_editor(strpos($state['error'], 'не найдена') !== false, 'Missing record is explained');
$calls = [];
$state = panel_editor_state('save', ['id'=>'42'], [], 'Write failed', $lookup, ['id'=>42, 'name'=>'Original']);
check_editor($state['action'] === 'edit' && !$calls, 'A failed write reuses the already loaded record');
$state = panel_editor_state('save', ['id'=>'42'], [], 'Save failed', static function (int $id): ?array { throw new RuntimeException('Unavailable'); });
check_editor($state['action'] === 'list' && strpos($state['error'], 'загрузить') !== false, 'Lookup failure does not offer an insert');
foreach (['api_tokens', 'wiki_terms', 'analytics', 'parsers'] as $action) {
    $state = panel_editor_state($action, [], [], '', $lookup);
    check_editor($state['action'] === $action && !$calls, 'Non-editor routes remain unchanged');
}
$state = panel_editor_state('save_wiki_terms', [], [], 'Rejected', $lookup);
check_editor($state['action'] === 'wiki_terms' && !$calls, 'Wiki errors reopen Wiki without card lookup');
$groups = ['mechanics'=>[['term_en'=>'Taunt', 'term_ru'=>'Old'], ['term_en'=>'Battlecry', 'term_ru'=>'Original']]];
$post = ['action'=>'save_wiki_terms', 'terms'=>['mechanics'=>[
    ['en'=>'Taunt', 'ru'=>'<edited>'], ['en'=>'Unknown', 'ru'=>'ignored'], ['en'=>'Battlecry', 'ru'=>['invalid']],
], 'unknown'=>[['en'=>'Injected', 'ru'=>'ignored']]]];
$restored = panel_wiki_editor_values($groups, $post, 'Rejected');
check_editor($restored['mechanics'][0]['term_ru'] === '<edited>', 'Wiki values survive a rejection verbatim for escaped form rendering');
check_editor($restored['mechanics'][1]['term_ru'] === 'Original' && count($restored) === 1 && count($restored['mechanics']) === 2, 'Unknown groups, terms and malformed values are ignored');
check_editor(panel_wiki_editor_values($groups, $post, '') === $groups, 'Successful Wiki saves use server values');
echo "OK: production editor recovery state\n";
