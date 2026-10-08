<?php
declare(strict_types=1);

function card_tile_number($value, int $minimum = 0): string
{
    if (is_bool($value) || (!is_int($value) && !is_float($value) && !is_string($value)) || !is_numeric($value)) return '—';
    $number = (float)$value;
    return is_finite($number) && floor($number) === $number && $number >= $minimum ? (string)(int)$number : '—';
}

function card_tile_palette(array $row): array
{
    $colors = ['beast'=>'#49633f','demon'=>'#56375e','dragon'=>'#753f37','elemental'=>'#386074',
        'mech'=>'#46586e','murloc'=>'#28675f','naga'=>'#3b477b','pirate'=>'#795839',
        'quilboar'=>'#75475a','undead'=>'#59603c'];
    $tribes = [];
    foreach (['creature_types','races','creature_type','minion_type','race'] as $field) {
        $value = $row[$field] ?? null;
        if (is_string($value)) {
            $decoded = json_decode($value, true);
            if (json_last_error() === JSON_ERROR_NONE) $value = $decoded;
        }
        $items = is_array($value) && !isset($value['slug']) ? $value : [$value];
        foreach ($items as $item) {
            if (is_array($item)) $item = $item['slug'] ?? null;
            if (!is_string($item)) continue;
            foreach (preg_split('/[\s,;\/|+]+/', strtolower($item)) as $tribe) {
                $tribe = $tribe === 'mechanical' ? 'mech' : ($tribe === 'quillboar' ? 'quilboar' : $tribe);
                $tribes[$tribe] = true;
            }
        }
    }
    if (isset($tribes['all'])) return ['#49633f','#28675f','#75475a','#605273'];
    ksort($tribes);
    $palette = [];
    foreach ($tribes as $tribe => $_) if (isset($colors[$tribe])) $palette[] = $colors[$tribe];
    return $palette ?: ['#606367'];
}

/** All dimensions are relative to height 64; width is chosen by the app. */
function card_tile_layout(array $row, string $entityType): array
{
    $assets = 'https://api.kolodahearthstone.com/assets/deck-tiles/';
    $bg = in_array($entityType, ['battleground_card','timewarped_card'], true);
    $type = strtolower((string)($row['card_type'] ?? ($bg ? 'minion' : 'constructed')));
    $spell = in_array($type, ['spell','battleground_spell','tavern_spell'], true);
    $rarity = strtoupper((string)($row['rarity'] ?? ''));
    $legendary = in_array($rarity, ['LEGENDARY','5'], true);
    $colors = $bg ? ($spell ? ['#606367'] : card_tile_palette($row)) : [
        $legendary ? '#715022' : (in_array($rarity,['EPIC','4'],true) ? '#503961' : (in_array($rarity,['RARE','3'],true) ? '#23445b' : '#606367'))
    ];
    $name = 'Без названия';
    foreach (['name_ru','name','name_russian','name_en','pet_name_ru','variant_name','pet_name','coin_name_ru','coin_name_en'] as $key) {
        if (!empty($row[$key])) { $name = (string)$row[$key]; break; }
    }
    $cost = $bg ? card_tile_number($row['tavern_tier'] ?? $row['tier'] ?? null, 1) : card_tile_number($row['mana_cost'] ?? $row['cost'] ?? null);
    $price = $bg ? ($spell ? card_tile_number($row['cost'] ?? $row['mana_cost'] ?? null) : ($type === 'minion' ? '3' : '')) : ($legendary ? '★' : '');
    $stops = [];
    if (count($colors) > 1) {
        $positions = $colors === ['#49633f','#28675f','#75475a','#605273'] ? [0, .35, .7, 1] : array_map(static fn(int $i): float => $i / (count($colors)-1), array_keys($colors));
        foreach ($colors as $i => $color) $stops[] = ['color'=>$color,'position'=>$positions[$i]];
    }
    $textColor = $bg ? '#ffe2a0' : '#ffffff';
    return [
        'version'=>1, 'reference_size'=>['width'=>320,'height'=>64], 'min_width'=>200,
        'corner_radius'=>3, 'vertical_alignment'=>'center',
        'background'=>['color'=>end($colors), 'gradient'=>$stops ? ['angle_deg'=>110,'stops'=>$stops] : null],
        'art'=>['url'=>$row['horizontal_image_url'] ?? null, 'aspect_ratio'=>5, 'anchor'=>'right', 'end_inset'=>34, 'fit'=>'contain'],
        'text_style'=>['outline_color'=>'#18130f','outline_width'=>1,'shadow'=>['color'=>'#000000','offset_y'=>2,'blur'=>2]],
        'cost'=>['text'=>$cost,'color'=>$textColor,'column_width'=>40,'font_url'=>$assets.'hearthstone-belwe.ttf?v=1','font_size'=>28,
            'outline_color'=>$bg ? '#392008' : '#102746',
            'icon'=>!$bg && $cost !== '—' ? ['url'=>$assets.'mana-crystal.png?v=1','width'=>40,'height'=>42] : null],
        'title'=>['text'=>$name,'color'=>'#ffffff','font_url'=>$assets.(preg_match('/[А-Яа-яЁё]/u',$name) ? 'hearthstone-benguiat.ttf' : 'hearthstone-belwe.ttf').'?v=1',
            'font_size'=>21,'font_weight'=>700,'inset_start'=>48,'inset_end'=>36,'max_lines'=>1,'overflow'=>'ellipsis'],
        'badge'=>['text'=>$price,'kind'=>$bg ? ($price === '' ? 'none' : 'gold') : ($legendary ? 'legendary' : 'none'),
            'color'=>$textColor,'column_width'=>36,'font_url'=>$assets.'hearthstone-benguiat.ttf?v=1','font_size'=>27,
            'icon'=>!$bg && $legendary ? ['url'=>$assets.'legendary-star.png?v=1','width'=>32,'height'=>32] : null],
    ];
}
