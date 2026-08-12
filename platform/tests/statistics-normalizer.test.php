#!/usr/bin/env php
<?php
declare(strict_types=1);

require __DIR__ . '/../scripts/statistics-normalizer.php';

function expect(bool $condition, string $message): void
{
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$meta = [
    'fetched_at' => '2026-08-07T10:00:00+00:00',
    'data' => ['structured' => [
        'type' => 'hsguru_meta_matrix',
        'slices' => [[
            'key' => 'standard|legend|past_day|any_player',
            'format' => 'standard',
            'rank' => 'legend',
            'period' => 'past_day',
            'coin' => 'any_player',
            'source_url' => 'https://www.hsguru.com/meta',
            'rows' => [[
                'archetype' => 'Test Rogue',
                'winrate' => 54.2,
                'popularity' => 8.3,
                'games' => 1234,
                'turns' => 8.4,
                'duration_minutes' => 9.1,
                'climbing_speed' => 0.7,
            ]],
        ]],
        'current_catalog' => [
            'criteria' => ['period' => 'patch_36.2.0', 'rank' => 'all'],
            'archetypes' => [[
                'format' => 'standard',
                'archetype' => 'Test Rogue',
                'games' => 1234,
                'winrate' => 54.2,
                'popularity_pct' => 8.3,
                'deck_count' => 4,
                'has_decks' => true,
                'archetype_url' => 'https://www.hsguru.com/archetype/Test%20Rogue',
            ]],
        ],
    ]],
];
$snapshots = normalize_statistics_dataset('hsguru_meta_matrix', 'meta-v1', $meta);
expect(count($snapshots) === 2, 'HSGuru produces a meta slice and an archetype catalog');
expect($snapshots[0]['domain'] === 'constructed_meta', 'meta domain is preserved');
expect($snapshots[0]['rank_range'] === 'legend', 'meta rank is preserved');
expect($snapshots[0]['rows'][0]['win_rate'] === 54.2, 'meta win rate is numeric');
expect($snapshots[1]['domain'] === 'constructed_archetypes', 'catalog domain is produced');
expect($snapshots[1]['rows'][0]['metrics']['has_decks'] === true, 'catalog deck availability is preserved');

$arena = [
    'fetched_at' => '2026-08-07T16:00:00+00:00',
    'data' => ['structured' => [
        'type' => 'arena_card_tiers',
        'cards' => [[
            'id' => 'CARD_001',
            'dbfId' => 42,
            'name' => 'Тестовая карта',
            'cardClass' => 'MAGE',
            'tier' => 'A',
            'win_rate' => 57.5,
            'pick_rate' => '61.4%',
            'times_played' => 900,
            'image_url' => 'https://art.hearthstonejson.com/card.png',
        ]],
    ]],
];
$snapshots = normalize_statistics_dataset('firestone_arena_cards_normal', 'arena-v1', $arena);
expect(count($snapshots) === 1, 'Arena produces one snapshot');
expect($snapshots[0]['domain'] === 'arena_cards', 'Arena domain is preserved');
expect($snapshots[0]['mode'] === 'regular', 'Regular Arena mode is explicit');
expect($snapshots[0]['rows'][0]['pick_rate'] === 61.4, 'percent strings become numeric');
expect($snapshots[0]['rows'][0]['image_url'] !== '', 'card art remains available to the UI');

$cards = [
    'fetched_at' => '2026-08-07T16:01:00+00:00',
    'data' => ['structured' => [
        'type' => 'card_stats',
        'game_type' => 'RANKED_WILD',
        'rank_range' => 'DIAMOND_4_TO_1',
        'time_range' => 'LAST_7_DAYS',
        'cards' => [[
            'id' => 'WILD_001',
            'dbfId' => 84,
            'name' => 'Wild Card',
            'deck_winrate' => '51.25%',
            'deck_popularity' => '3.75%',
            'times_played' => 700,
        ]],
    ]],
];
$snapshots = normalize_statistics_dataset('hsreplay_cards_wild_diamond_4_1_7d', 'cards-v1', $cards);
expect($snapshots[0]['format_name'] === 'wild', 'Wild format is preserved');
expect($snapshots[0]['rank_range'] === 'DIAMOND_4_TO_1', 'constructed-card rank is preserved');
expect($snapshots[0]['period'] === 'LAST_7_DAYS', 'constructed-card period is preserved');

$heroes = [
    'fetched_at' => '2026-08-07T12:10:00+00:00',
    'data' => ['structured' => [
        'type' => 'bg_heroes',
        'mmr' => 'mmr-10',
        'time_period' => 'past-three',
        'heroes' => [[
            'id' => 'HERO_001',
            'dbfId' => 126,
            'hero' => 'Тестовый герой',
            'avg_placement' => 3.72,
            'pick_rate_value' => 22.1,
            'games' => 456,
        ]],
    ]],
];
$snapshots = normalize_statistics_dataset('firestone_bg_heroes_mmr_10', 'heroes-v1', $heroes);
expect($snapshots[0]['domain'] === 'bg_heroes', 'hero domain is preserved');
expect($snapshots[0]['rating_bracket'] === 'TOP_10_PERCENT', 'hero rating bracket is explicit');
expect($snapshots[0]['rows'][0]['avg_placement'] === 3.72, 'hero placement is numeric');

fwrite(STDOUT, "OK: statistics normalizer contract\n");
