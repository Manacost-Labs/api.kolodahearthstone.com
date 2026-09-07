<?php
declare(strict_types=1);

/** Load only the current workspace; dependency order matters for deferred scripts. */
function panel_script_assets(string $action): array
{
    $common = ['/assets/workspace.js?v=1', '/assets/panel-ui.js?v=4'];
    $sections = [
        'list' => ['/assets/table-controls.js?v=4', '/assets/media-preview.js?v=1', '/assets/catalog-reader.js?v=3'],
        'analytics' => ['/assets/table-controls.js?v=4', '/assets/parsing-reliability.js?v=10', '/assets/analytics.js?v=16', '/assets/media-preview.js?v=1'],
        'parsers' => ['/assets/parser-control-view.js?v=3', '/assets/parser-control.js?v=4'],
        'api_tokens' => ['/assets/table-controls.js?v=4', '/assets/token-controls.js?v=1'],
        'new' => ['/assets/editor-controls.js?v=1'],
        'edit' => ['/assets/editor-controls.js?v=1'],
        'wiki_terms' => ['/assets/editor-controls.js?v=1'],
    ];
    return array_merge($common, $sections[$action] ?? []);
}
