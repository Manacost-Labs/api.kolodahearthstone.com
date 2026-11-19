<?php
declare(strict_types=1);

/**
 * Read-only CLI around panel/lib/analytics.php (never deployed: deploy-panel.sh skips tests/).
 *
 *   php panel/tests/analytics_cli.php --registry
 *   php panel/tests/analytics_cli.php --smoke
 *   php panel/tests/analytics_cli.php --module <key> --query 'card_name=Матриарх'
 *
 * Only GET-style reads of the internal Data API are performed; no secret is ever printed.
 */

if (PHP_SAPI !== 'cli') {
    exit(1);
}

require dirname(__DIR__) . '/lib/analytics.php';

const ANALYTICS_CLI_JSON = JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE;

/** The registry as Next receives it from next-data.php?action=analytics_registry. */
function analytics_cli_registry(): array
{
    $modules = [];
    foreach (analytics_module_registry() as $key => $definition) {
        $modules[$key] = array_intersect_key($definition, array_flip(['title', 'description', 'params']));
    }

    return ['modules' => $modules];
}

function analytics_cli_input(string $query): array
{
    parse_str($query, $input);

    return $input;
}

function analytics_cli_smoke(): int
{
    $failed = 0;
    foreach (array_keys(analytics_module_registry()) as $module) {
        if ($module === 'providers') {
            require_once dirname(__DIR__) . '/lib/parser_control.php';
            if (panel_parser_control_config() === null) {
                echo "providers skip no-parser-control-config\n";
                continue;
            }
        }
        $input = $module === 'card' ? ['card_name' => 'Матриарх'] : [];
        try {
            $response = analytics_module_response($module, $input);
            $rows = is_array($response['rows'] ?? null) ? count($response['rows']) : 0;
            $usable = ($response['ok'] ?? false) === true && ($response['title'] ?? '') !== '';
            echo $module . ' ' . ($usable ? 'ok' : 'fail') . ' ' . $rows . "\n";
            $failed += $usable ? 0 : 1;
        } catch (Throwable $exception) {
            // Class and code only: a message may carry a URL, DSN or token.
            $code = (int)$exception->getCode();
            echo $module . ' fail ' . get_class($exception) . ($code !== 0 ? ' code=' . $code : '') . "\n";
            $failed++;
        }
    }

    return $failed === 0 ? 0 : 1;
}

$options = getopt('', ['registry', 'smoke', 'module:', 'query:']);
if (isset($options['registry'])) {
    echo json_encode(analytics_cli_registry(), ANALYTICS_CLI_JSON | JSON_PRETTY_PRINT) . "\n";
    exit(0);
}
if (isset($options['smoke'])) {
    exit(analytics_cli_smoke());
}
if (isset($options['module']) && is_string($options['module'])) {
    $query = is_string($options['query'] ?? null) ? $options['query'] : '';
    try {
        $response = analytics_module_response($options['module'], analytics_cli_input($query));
        echo json_encode($response, ANALYTICS_CLI_JSON) . "\n";
    } catch (InvalidArgumentException $exception) {
        echo json_encode(['ok' => false, 'message' => $exception->getMessage(), 'status' => 422], ANALYTICS_CLI_JSON) . "\n";
        exit(2);
    } catch (Throwable $exception) {
        echo json_encode(['ok' => false, 'message' => 'Не удалось загрузить статистику.', 'status' => 502], ANALYTICS_CLI_JSON) . "\n";
        exit(3);
    }
    exit(0);
}
fwrite(STDERR, "Usage: --registry | --smoke | --module <key> [--query 'a=b&c=d']\n");
exit(64);
