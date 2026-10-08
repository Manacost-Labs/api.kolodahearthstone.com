<?php
declare(strict_types=1);

/** If-None-Match takes precedence over If-Modified-Since, including a miss. */
function catalog_not_modified(string $etag, ?string $lastModifiedHttp, array $server): bool
{
    $header = trim((string)($server['HTTP_IF_NONE_MATCH'] ?? ''));
    if ($header !== '') {
        foreach (explode(',', $header) as $candidate) {
            $candidate = trim($candidate);
            if ($candidate === '*' || $candidate === $etag || $candidate === 'W/' . $etag) return true;
        }
        return false;
    }
    $since = $server['HTTP_IF_MODIFIED_SINCE'] ?? null;
    if (!is_string($since) || $lastModifiedHttp === null) return false;
    $client = strtotime($since);
    $modified = strtotime($lastModifiedHttp);
    return $client !== false && $modified !== false && $client >= $modified;
}
