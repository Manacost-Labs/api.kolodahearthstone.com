<?php
declare(strict_types=1);

/**
 * Compare published parser dataset files with imported raw.datasets sources.
 *
 * A file that was never imported is an error: the importer is behind or broken.
 * A raw source without a file is history of a dataset that its source stopped
 * publishing (for example a status-only source whose legacy file was removed);
 * it is reported as stale instead of failing every synchronization.
 *
 * @param list<string> $fileSources source IDs of datasets/*.json
 * @param list<string> $rawSources distinct raw.datasets source IDs
 * @return array{0: list<string>, 1: list<string>} [errors, warnings]
 */
function compareRawDatasets(array $fileSources, array $rawSources): array
{
    $files = array_values(array_unique($fileSources));
    $raw = array_values(array_unique($rawSources));
    $notImported = array_values(array_diff($files, $raw));
    $stale = array_values(array_diff($raw, $files));
    sort($notImported);
    sort($stale);
    $errors = [];
    $warnings = [];
    if ($notImported !== []) {
        $errors[] = 'raw datasets: files without an import: ' . implode(', ', $notImported);
    }
    foreach ($stale as $source) {
        $warnings[] = "raw datasets: {$source} has no published file (stale history)";
    }
    return [$errors, $warnings];
}

/** @return list<string> */
function datasetFileSources(string $directory): array
{
    return array_map(
        static fn(string $path): string => basename($path, '.json'),
        glob(rtrim($directory, '/') . '/*.json') ?: [],
    );
}
