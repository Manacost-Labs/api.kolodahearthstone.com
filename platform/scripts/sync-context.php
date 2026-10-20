<?php
declare(strict_types=1);

function syncApplicationName(): string
{
    $name = getenv('HS_DATA_SYNC_APPLICATION_NAME');
    if ($name === false || $name === '') {
        return 'hsdp-manual';
    }
    if (!preg_match('/^hsdp-sync-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/D', $name)) {
        throw new RuntimeException('Invalid scheduled synchronization identity');
    }
    return $name;
}
