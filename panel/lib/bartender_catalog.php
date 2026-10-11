<?php
declare(strict_types=1);

function bartender_skin_to_api(array $row): array
{
    return ['card_id'=>$row['card_id'], 'dbf'=>$row['dbf'] === null ? null : (int)$row['dbf'],
        'parent_card_id'=>$row['parent_card_id'], 'name'=>['ru'=>$row['name_ru'], 'en'=>$row['name_en']],
        'image_url'=>$row['image_url'], 'status'=>$row['status'], 'source'=>$row['source'],
        'media_verified'=>$row['image_url'] !== null, 'updated_at'=>$row['updated_at']];
}

function bartender_skin_listing(PDO $pdo, int $page, int $perPage): array
{
    $page = max(1, min(1000000, $page));
    $perPage = max(1, min(200, $perPage));
    $summary = $pdo->query('SELECT COUNT(*) AS total, MAX(updated_at) AS last_updated FROM battlegrounds_bartender_skins')->fetch(PDO::FETCH_ASSOC);
    $rows = $pdo->query('SELECT * FROM battlegrounds_bartender_skins ORDER BY card_id LIMIT ' . $perPage . ' OFFSET ' . (($page - 1) * $perPage))->fetchAll(PDO::FETCH_ASSOC);
    return ['data'=>array_map('bartender_skin_to_api', $rows), 'last_updated'=>$summary['last_updated'],
        'pagination'=>['page'=>$page, 'per_page'=>$perPage, 'total'=>(int)$summary['total'],
            'total_pages'=>(int)ceil((int)$summary['total'] / $perPage)]];
}
