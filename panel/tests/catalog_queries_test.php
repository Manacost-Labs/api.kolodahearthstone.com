<?php
declare(strict_types=1);
require __DIR__ . '/../lib/catalog_queries.php';
foreach (['Авиана', 'Aviana', 'AT_045', "quote' OR 1=1 --"] as $query) {
    $filter = constructed_search_filter($query);
    preg_match_all('/:(q_[a-z_]+)/', $filter['where'], $matches);
    if (count($matches[1]) !== 8 || count(array_unique($matches[1])) !== 8) throw new RuntimeException('Native PDO search placeholders must be unique');
    if ($matches[1] !== array_keys($filter['parameters'])) throw new RuntimeException('Search bindings do not match both pagination queries');
    if (array_values(array_unique($filter['parameters'])) !== ['%' . $query . '%']) throw new RuntimeException('Search text must remain bound data');
    if (strpos($filter['where'], $query) !== false) throw new RuntimeException('Search text was interpolated into SQL');
}
echo "OK: constructed search native PDO bindings\n";
