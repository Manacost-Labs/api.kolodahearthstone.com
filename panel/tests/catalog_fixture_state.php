<?php
declare(strict_types=1);
// Only fixture data/URL adapters; production SQL and POST routes are never loaded.
require_once __DIR__ . '/../lib/catalog_view.php';
require_once __DIR__ . '/../lib/catalog_navigation.php';
$sort = panel_catalog_sort($_GET['sort'] ?? null);
function query_url(array $overrides = []): string {
    $query = array_merge($_GET, $overrides);
    unset($query['action'], $query['id']);
    foreach ($query as $key=>$value) {
        if ($value === null || $value === '' || ($key === 'page' && (int)$value <= 1)) unset($query[$key]);
    }
    return '/tests/catalog_panel_fixture.php' . ($query ? '?' . http_build_query($query) : '');
}
$cardType = (string)($_GET['card_type'] ?? '');
if (!isset(filter_card_types()[$cardType])) $cardType = '';
$q = (string)($_GET['q'] ?? '');
$tier = (string)($_GET['tier'] ?? '');
$creatureType = (string)($_GET['creature_type'] ?? '');
$pool = (string)($_GET['pool'] ?? '');
$duos = (string)($_GET['duos'] ?? '');
$media = (string)($_GET['media'] ?? '');
$skinRarity = (string)($_GET['rarity'] ?? '');
$constructedFormat = (string)($_GET['constructed_format'] ?? 'all');
$showHeroes = $cardType === 'hero'; $showHeroSkins = $cardType === 'hero_skin';
$showPets = $cardType === 'pet'; $showCoins = $cardType === 'coin';
$showTimewarped = $cardType === 'timewarped'; $showConstructed = $cardType === 'constructed';
$showLibrary = in_array($cardType, ['anomaly','quest','darkmoon_prize','reward','trinket'], true);
$libraryType = $showLibrary ? $cardType : '';
$perPage = (int)($_GET['per_page'] ?? 50);
if (!in_array($perPage, [25,50,100,150], true)) $perPage = 50;
$mediaLabels = ['has_buddy'=>'С компаньоном', 'wiki_error'=>'Ошибки wiki'];
$skinRarityLabels = ['basic'=>'Базовый', 'legendary'=>'Легендарный'];
$skinMediaLabels = ['animated'=>'Есть Animated', 'gallery'=>'Есть Gallery'];
$resetUrl = '/tests/catalog_panel_fixture.php' . ($cardType !== '' ? '?card_type=' . rawurlencode($cardType) : '');
$workspaceTitle = filter_card_types()[$cardType] ?? 'Карты Полей сражений';
$activeFilters = [];
foreach (['q'=>$q, 'tier'=>$tier, 'creature_type'=>$creatureType, 'pool'=>$pool, 'media'=>$media] as $key=>$value) {
    if ($value !== '') $activeFilters[] = ['label'=>$key . ': ' . $value, 'href'=>query_url([$key=>null, 'page'=>null])];
}
$names = ['Мурлок-разведчик', 'Золотой дракон', 'Ночной охотник', 'Пират таверны', 'Механический страж', 'Древний элементаль'];
$fixtureRows = [];
for ($i=1; $i<=120; $i++) {
    $row = ['name'=>$names[($i-1)%count($names)], 'id'=>'BG_FIXTURE_' . $i, 'tier'=>(string)(1+($i-1)%6), 'updated_at'=>$i];
    if ($q !== '' && mb_stripos($row['name'] . ' ' . $row['id'], $q) === false) continue;
    if ($tier !== '' && $row['tier'] !== $tier) continue;
    $fixtureRows[] = $row;
}
if (isset($_GET['empty'])) $fixtureRows = [];
if ($sort !== 'default') {
    usort($fixtureRows, static function (array $a, array $b) use ($sort): int {
        if ($sort === 'updated_desc') return $b['updated_at'] <=> $a['updated_at'];
        $order = strcmp($a['name'], $b['name']);
        if ($sort === 'name_desc') $order *= -1;
        return $order ?: strnatcmp($a['id'], $b['id']);
    });
}
$filteredTotal = count($fixtureRows);
$totalPages = max(1, (int)ceil($filteredTotal / $perPage));
$page = max(1, min($totalPages, (int)($_GET['page'] ?? 1)));
$fixtureRows = array_slice($fixtureRows, ($page-1)*$perPage, $perPage);
$pageFrom = $filteredTotal ? ($page-1)*$perPage+1 : 0;
$pageTo = min($pageFrom + count($fixtureRows)-1, $filteredTotal);
if (!$filteredTotal) $pageTo = 0;
$pageWindowStart = max(1, $page-2); $pageWindowEnd = min($totalPages, $page+2);
$fixtureEmpty = $filteredTotal === 0;
