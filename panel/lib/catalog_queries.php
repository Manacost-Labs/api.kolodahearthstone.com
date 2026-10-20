<?php
declare(strict_types=1);

/** Native PDO prepares require a different placeholder for every occurrence. */
function constructed_search_filter(string $query): array
{
    $clauses = [];
    $parameters = [];
    foreach (['name_ru','name_en','card_id','dbf','text_ru','text_en','flavor_ru','flavor_en'] as $column) {
        $parameter = 'q_' . $column;
        $clauses[] = 'c.' . $column . ' LIKE :' . $parameter;
        $parameters[$parameter] = '%' . $query . '%';
    }
    return ['where'=>'(' . implode(' OR ', $clauses) . ')','parameters'=>$parameters];
}
