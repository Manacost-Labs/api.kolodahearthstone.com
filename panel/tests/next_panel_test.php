<?php
declare(strict_types=1);
require __DIR__ . '/../lib/next_panel.php';
class CostFixtureStatement extends PDOStatement
{
    public function execute(?array $params = null): bool
    {
        if ($params !== ['SPELL_ZERO', 'SPELL_EN', 'SPELL_BAD', 'SPELL_MISSING']) throw new RuntimeException('Cost lookup must be restricted to displayed cards.');
        return true;
    }
    public function fetchAll(int $mode = PDO::FETCH_DEFAULT, mixed ...$args): array
    {
        return [
            ['card_id' => 'SPELL_ZERO', 'payload_json' => json_encode(['ru' => ['cost' => 0]])],
            ['card_id' => 'SPELL_EN', 'payload_json' => json_encode(['ru' => [], 'en' => ['cost' => 5]])],
            ['card_id' => 'SPELL_BAD', 'payload_json' => json_encode(['ru' => ['cost' => false]])],
        ];
    }
}
class CostFixturePDO extends PDO
{
    public function __construct() {}
    public function prepare(string $query, array $options = []): PDOStatement|false { return new CostFixtureStatement(); }
}
$database = new CostFixturePDO();
$spells = array_map(static fn(string $id): array => ['card_id' => $id, 'card_type' => 'spell'], ['SPELL_ZERO', 'SPELL_EN', 'SPELL_BAD', 'SPELL_MISSING']);
$priced = panel_next_attach_purchase_costs($database, $spells, 'spell');
if (array_column($priced, 'cost') !== [0, 5, null, null]) throw new RuntimeException('Spell cost must use the latest HearthstoneJSON record and preserve free spells.');
if (panel_next_attach_purchase_costs($database, $spells, 'constructed') !== $spells) throw new RuntimeException('Constructed mana changed.');
foreach (['', 'minion', 'spell'] as $type) {
    $rows = panel_next_enrich_records([['dbf' => 42, 'card_id' => 'BG_TEST']], $type, [42 => ['card_id' => 'BG_TEST_G']], ['BG_TEST' => ['name_ru' => 'Тест']]);
    if ($rows[0]['golden_variant']['card_id'] !== 'BG_TEST_G' || $rows[0]['wiki']['name_ru'] !== 'Тест') throw new RuntimeException('Filtered BG records lost enrichment.');
}
$pet = [['card_id' => 'PET_TEST']];
if (panel_next_enrich_records($pet, 'pet', [], []) !== $pet) throw new RuntimeException('Non-BG records changed.');
$page = panel_next_page([
    'action' => 'list', 'records' => [['card_id' => 'BG_TEST', 'attack' => 0]],
    'user' => ['id' => 42, 'login' => 'Zulut30', 'access_token' => 'must-not-export'],
    'config' => ['password' => 'must-not-export'],
    'extra' => ['managerConfig' => ['credential' => 'must-not-export']],
]);
if (str_contains(json_encode($page), 'must-not-export')) throw new RuntimeException('Private server state leaked.');
if ($page['records'][0]['attack'] !== 0 || $page['user'] !== ['id' => 42, 'login' => 'Zulut30']) throw new RuntimeException('Presentation identity changed.');
$issued = panel_next_page(['extra' => ['issuedToken' => ['token' => 'one-time-fixture']]]);
if ($issued['issuedToken']['token'] !== 'one-time-fixture') throw new RuntimeException('One-time issuance missing.');
echo "OK: Next panel presentation contract\n";
