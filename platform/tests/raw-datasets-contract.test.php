<?php
declare(strict_types=1);

require __DIR__ . '/../scripts/raw-datasets-contract.php';

function check(bool $condition, string $message): void
{
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}" . PHP_EOL);
        exit(1);
    }
}

[$errors, $warnings] = compareRawDatasets(['a', 'b'], ['a', 'b']);
check($errors === [] && $warnings === [], 'matching sets pass silently');

// A status-only source whose legacy dataset file was removed keeps raw history.
[$errors, $warnings] = compareRawDatasets(['a'], ['a', 'hsreplay_battlegrounds_compositions_screenshot']);
check($errors === [], 'raw history without a file must not fail verification');
check(count($warnings) === 1 && str_contains($warnings[0], 'compositions_screenshot'), 'stale raw source is reported');

[$errors, $warnings] = compareRawDatasets(['a', 'new_source'], ['a']);
check(count($errors) === 1 && str_contains($errors[0], 'new_source'), 'a file that was never imported is an error');

$dir = sys_get_temp_dir() . '/raw-datasets-' . bin2hex(random_bytes(4));
mkdir($dir);
touch($dir . '/x.json');
touch($dir . '/notes.txt');
check(datasetFileSources($dir) === ['x'], 'only *.json files are dataset sources');
unlink($dir . '/x.json');
unlink($dir . '/notes.txt');
rmdir($dir);

fwrite(STDOUT, 'raw datasets contract tests passed' . PHP_EOL);
