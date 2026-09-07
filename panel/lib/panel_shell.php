<?php
declare(strict_types=1);

/** Static interface artwork; caller-provided strings never enter SVG markup. */
function panel_icon(string $name): string
{
    $paths = [
        'catalog' => 'M5 4h14v16H5zM9 4v16M5 9h14',
        'chart' => 'M4 4v16h16M8 15v-4m5 4V7m5 8v-6',
        'source' => 'M8 8H6a4 4 0 0 0 0 8h2m8-8h2a4 4 0 0 1 0 8h-2M8 12h8',
        'key' => 'M14 10a5 5 0 1 1-3-5l9 9v4h-4v-3h-3',
        'menu' => 'M4 6h16M4 12h16M4 18h16',
        'search' => 'm20 20-4.5-4.5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
    ];
    return '<svg class="panel-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="' . ($paths[$name] ?? $paths['catalog']) . '"/></svg>';
}
