<?php
declare(strict_types=1);

set_time_limit(0);
ini_set('memory_limit', '512M');
umask(0022);

$config = require __DIR__ . '/../config.php';

const HSJ_RU_URL = 'https://api.hearthstonejson.com/v1/latest/ruRU/cards.json';
const HSJ_EN_URL = 'https://api.hearthstonejson.com/v1/latest/enUS/cards.json';
const HSJ_RENDER_BASE = 'https://art.hearthstonejson.com/v1/render/latest/ruRU/512x/';
const HSJ_BGS_RENDER_BASE = 'https://art.hearthstonejson.com/v1/bgs/latest/ruRU/512x/';
const HSJ_ORIG_BASE = 'https://art.hearthstonejson.com/v1/orig/';
const SOURCE_HSJ = 'hearthstonejson';
const SOURCE_BLIZZARD = 'blizzard';
const FRAMED_RENDER_RECIPE_VERSION = '4-aspect-safe-original-art';

function db(array $config): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }

    $pdo = new PDO($config['db']['dsn'], $config['db']['user'], $config['db']['password'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);

    return $pdo;
}

function cli_option(string $name, ?string $default = null): ?string
{
    foreach ($_SERVER['argv'] ?? [] as $arg) {
        if (strpos($arg, '--' . $name . '=') === 0) {
            return substr($arg, strlen($name) + 3);
        }
    }

    return $default;
}

function ensure_schema(PDO $pdo): void
{
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS battlegrounds_card_import_runs (
            id INT UNSIGNED NOT NULL AUTO_INCREMENT,
            source VARCHAR(32) NOT NULL,
            started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP NULL DEFAULT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'running',
            scanned INT UNSIGNED NOT NULL DEFAULT 0,
            inserted INT UNSIGNED NOT NULL DEFAULT 0,
            updated INT UNSIGNED NOT NULL DEFAULT 0,
            changed INT UNSIGNED NOT NULL DEFAULT 0,
            error TEXT DEFAULT NULL,
            PRIMARY KEY (id),
            KEY idx_source_started (source, started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ");

    $pdo->exec("
        CREATE TABLE IF NOT EXISTS battlegrounds_card_changes (
            id INT UNSIGNED NOT NULL AUTO_INCREMENT,
            card_id VARCHAR(64) NOT NULL,
            source VARCHAR(32) NOT NULL,
            old_hash CHAR(64) DEFAULT NULL,
            new_hash CHAR(64) DEFAULT NULL,
            change_type VARCHAR(16) NOT NULL,
            payload_json JSON DEFAULT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_card_created (card_id, created_at),
            KEY idx_source_created (source, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ");

    $pdo->exec("
        ALTER TABLE battlegrounds_cards
            ADD COLUMN IF NOT EXISTS variant_kind VARCHAR(16) NOT NULL DEFAULT 'base' AFTER card_type,
            ADD COLUMN IF NOT EXISTS base_dbf INT UNSIGNED DEFAULT NULL AFTER variant_kind,
            ADD COLUMN IF NOT EXISTS base_card_id VARCHAR(64) DEFAULT NULL AFTER base_dbf,
            ADD COLUMN IF NOT EXISTS premium_dbf INT UNSIGNED DEFAULT NULL AFTER base_card_id,
            ADD INDEX IF NOT EXISTS idx_bg_variant_kind (variant_kind),
            ADD INDEX IF NOT EXISTS idx_bg_base_dbf (base_dbf),
            ADD INDEX IF NOT EXISTS idx_bg_base_card_id (base_card_id),
            ADD INDEX IF NOT EXISTS idx_bg_premium_dbf (premium_dbf)
    ");
}

function start_run(PDO $pdo, string $source): int
{
    $stmt = $pdo->prepare('INSERT INTO battlegrounds_card_import_runs (source) VALUES (:source)');
    $stmt->execute(['source' => $source]);

    return (int)$pdo->lastInsertId();
}

function finish_run(PDO $pdo, int $runId, array $stats, string $status = 'ok', ?string $error = null): void
{
    $stmt = $pdo->prepare("
        UPDATE battlegrounds_card_import_runs
        SET finished_at = CURRENT_TIMESTAMP,
            status = :status,
            scanned = :scanned,
            inserted = :inserted,
            updated = :updated,
            changed = :changed,
            error = :error
        WHERE id = :id
    ");
    $stmt->execute([
        'status' => $status,
        'scanned' => (int)($stats['scanned'] ?? 0),
        'inserted' => (int)($stats['inserted'] ?? 0),
        'updated' => (int)($stats['updated'] ?? 0),
        'changed' => (int)($stats['changed'] ?? 0),
        'error' => $error,
        'id' => $runId,
    ]);
}

function fetch_json(string $url, array $headers = []): array
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_CONNECTTIMEOUT => 20,
        CURLOPT_TIMEOUT => 90,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_USERAGENT => 'db.kolodahs.ru-card-scan/1.0',
    ]);
    $body = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);

    if (!is_string($body) || $status < 200 || $status >= 300) {
        throw new RuntimeException('HTTP ' . $status . ' while fetching ' . redact_url($url) . ($error ? ': ' . $error : ''));
    }

    $json = json_decode($body, true);
    if (!is_array($json)) {
        throw new RuntimeException('Invalid JSON from ' . $url);
    }

    return $json;
}

function fetch_binary(string $url): ?string
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_CONNECTTIMEOUT => 20,
        CURLOPT_TIMEOUT => 90,
        CURLOPT_USERAGENT => 'db.kolodahs.ru-card-scan/1.0',
    ]);
    $body = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    return is_string($body) && $status >= 200 && $status < 300 ? $body : null;
}

function redact_url(string $url): string
{
    return preg_replace('/([?&]access_token=)[^&]+/i', '$1REDACTED', $url) ?? $url;
}

function fetch_text_post(string $url, array $postFields, array $headers = []): array
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => http_build_query($postFields),
        CURLOPT_CONNECTTIMEOUT => 20,
        CURLOPT_TIMEOUT => 60,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_USERAGENT => 'db.kolodahs.ru-card-scan/1.0',
    ]);
    $body = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);

    if (!is_string($body) || $status < 200 || $status >= 300) {
        throw new RuntimeException('HTTP ' . $status . ' while posting ' . $url . ($error ? ': ' . $error : ''));
    }

    $json = json_decode($body, true);
    if (!is_array($json)) {
        throw new RuntimeException('Invalid JSON from ' . $url);
    }

    return $json;
}

function clean_text($value): ?string
{
    $value = (string)($value ?? '');
    $value = preg_replace('/<br\s*\/?>/i', "\n", $value);
    $value = strip_tags($value ?? '');
    $value = html_entity_decode($value, ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $value = preg_replace('/[ \t]+/u', ' ', $value);
    $value = preg_replace('/\R{3,}/u', "\n\n", $value);
    $value = trim($value ?? '');

    return $value === '' ? null : $value;
}

function public_upload_path(string $folder, string $cardId, string $ext): string
{
    $safe = preg_replace('/[^A-Za-z0-9_.-]+/', '_', $cardId) ?? $cardId;

    return '/uploads/' . $folder . '/' . $safe . '.' . $ext;
}

function absolute_public_path(string $publicPath): string
{
    return dirname(__DIR__) . '/' . ltrim($publicPath, '/');
}

function ensure_parent_dir(string $path): void
{
    $dir = dirname($path);
    if (!is_dir($dir)) {
        mkdir($dir, 0755, true);
    }
}

function image_from_bytes(?string $bytes)
{
    if ($bytes === null || $bytes === '') {
        return null;
    }

    $image = @imagecreatefromstring($bytes);
    if (!$image) {
        return null;
    }
    imagepalettetotruecolor($image);
    imagesavealpha($image, true);

    return $image;
}

function resize_exact($source, int $width, int $height)
{
    $target = imagecreatetruecolor($width, $height);
    imagealphablending($target, false);
    imagesavealpha($target, true);
    $transparent = imagecolorallocatealpha($target, 0, 0, 0, 127);
    imagefilledrectangle($target, 0, 0, $width, $height, $transparent);

    imagecopyresampled(
        $target,
        $source,
        0,
        0,
        0,
        0,
        $width,
        $height,
        imagesx($source),
        imagesy($source)
    );

    return $target;
}

function resize_contain($source, int $width, int $height)
{
    $target = imagecreatetruecolor($width, $height);
    imagealphablending($target, false);
    imagesavealpha($target, true);
    $transparent = imagecolorallocatealpha($target, 0, 0, 0, 127);
    imagefilledrectangle($target, 0, 0, $width, $height, $transparent);

    $srcW = imagesx($source);
    $srcH = imagesy($source);
    $scale = min($width / $srcW, $height / $srcH);
    $newW = max(1, (int)round($srcW * $scale));
    $newH = max(1, (int)round($srcH * $scale));
    $dstX = (int)floor(($width - $newW) / 2);
    $dstY = (int)floor(($height - $newH) / 2);

    imagecopyresampled($target, $source, $dstX, $dstY, 0, 0, $newW, $newH, $srcW, $srcH);

    return $target;
}

/**
 * Resize an image so neither side exceeds the requested bounds while keeping
 * the original aspect ratio. Unlike resize_contain(), the returned canvas has
 * the natural scaled dimensions and therefore cannot introduce letterboxing.
 */
function resize_within($source, int $maxWidth, int $maxHeight)
{
    $srcW = imagesx($source);
    $srcH = imagesy($source);
    $scale = min($maxWidth / $srcW, $maxHeight / $srcH, 1.0);
    $width = max(1, (int)round($srcW * $scale));
    $height = max(1, (int)round($srcH * $scale));
    $target = imagecreatetruecolor($width, $height);
    imagealphablending($target, false);
    imagesavealpha($target, true);
    $transparent = imagecolorallocatealpha($target, 0, 0, 0, 127);
    imagefilledrectangle($target, 0, 0, $width, $height, $transparent);
    imagecopyresampled($target, $source, 0, 0, 0, 0, $width, $height, $srcW, $srcH);

    return $target;
}

function crop_image($source, int $x, int $y, int $width, int $height)
{
    $target = imagecreatetruecolor($width, $height);
    imagealphablending($target, false);
    imagesavealpha($target, true);
    $transparent = imagecolorallocatealpha($target, 0, 0, 0, 127);
    imagefilledrectangle($target, 0, 0, $width, $height, $transparent);
    imagecopy($target, $source, 0, 0, $x, $y, $width, $height);

    return $target;
}

function trim_light_border($source)
{
    $width = imagesx($source);
    $height = imagesy($source);
    if ($width < 10 || $height < 10) {
        return $source;
    }

    $corners = [
        imagecolorat($source, 0, 0),
        imagecolorat($source, $width - 1, 0),
        imagecolorat($source, 0, $height - 1),
        imagecolorat($source, $width - 1, $height - 1),
    ];
    $r = 0;
    $g = 0;
    $b = 0;
    foreach ($corners as $color) {
        $r += ($color >> 16) & 255;
        $g += ($color >> 8) & 255;
        $b += $color & 255;
    }
    $r = (int)round($r / 4);
    $g = (int)round($g / 4);
    $b = (int)round($b / 4);
    if ($r < 232 || $g < 232 || $b < 232) {
        return $source;
    }

    $minX = $width;
    $minY = $height;
    $maxX = -1;
    $maxY = -1;
    for ($y = 0; $y < $height; $y++) {
        for ($x = 0; $x < $width; $x++) {
            $color = imagecolorat($source, $x, $y);
            $pr = ($color >> 16) & 255;
            $pg = ($color >> 8) & 255;
            $pb = $color & 255;
            if (abs($pr - $r) + abs($pg - $g) + abs($pb - $b) > 42) {
                $minX = min($minX, $x);
                $minY = min($minY, $y);
                $maxX = max($maxX, $x);
                $maxY = max($maxY, $y);
            }
        }
    }

    if ($maxX < $minX || $maxY < $minY) {
        return $source;
    }

    $cropW = $maxX - $minX + 1;
    $cropH = $maxY - $minY + 1;
    if ($cropW >= $width - 4 && $cropH >= $height - 4) {
        return $source;
    }

    return crop_image($source, $minX, $minY, $cropW, $cropH);
}

function spell_frame_template_path(): ?string
{
    $preferred = [
        absolute_public_path('/uploads/framed/BG33_155.png'),
        absolute_public_path('/uploads/framed/BG25_020.png'),
    ];
    foreach ($preferred as $path) {
        if (is_file($path)) {
            return $path;
        }
    }

    $matches = glob(dirname(__DIR__) . '/uploads/framed/*.png');
    if (!is_array($matches) || !$matches) {
        return null;
    }

    sort($matches);

    return $matches[0];
}

function spell_frame_reference_paths(): array
{
    $ids = [
        'BG28_300',
        'BG25_001',
        'BG25_022',
        'BG25_008',
        'BG25_011',
        'BG34_231',
        'BG25_010',
        'BG28_309',
        'BG30_125',
        'BG33_323',
        'BG26_146',
        'BG32_330',
        'BG35_814',
        'BG25_013',
        'BG31_815',
        'BG31_803',
        'BG20_301',
        'BGS_004',
        'BG31_330',
        'BG32_236',
    ];

    $paths = [];
    foreach ($ids as $id) {
        $path = absolute_public_path('/uploads/framed/' . $id . '.png');
        if (is_file($path)) {
            $paths[] = $path;
        }
    }

    if (count($paths) >= 6) {
        return $paths;
    }

    $matches = glob(dirname(__DIR__) . '/uploads/framed/*.png');
    if (is_array($matches)) {
        sort($matches);
        foreach ($matches as $path) {
            $name = basename($path);
            if (strpos($name, '_') === 0 || strpos($name, 'BGS_Treasures_') === 0) {
                continue;
            }
            if (!in_array($path, $paths, true)) {
                $paths[] = $path;
            }
            if (count($paths) >= 20) {
                break;
            }
        }
    }

    return $paths;
}

function build_minion_frame_overlay(string $targetPath): bool
{
    $paths = spell_frame_reference_paths();
    if (count($paths) < 3) {
        return false;
    }

    $images = [];
    foreach ($paths as $path) {
        $image = @imagecreatefrompng($path);
        if (!$image || imagesx($image) !== 300 || imagesy($image) !== 350) {
            if ($image) {
                imagedestroy($image);
            }
            continue;
        }
        imagepalettetotruecolor($image);
        imagealphablending($image, false);
        imagesavealpha($image, true);
        $images[] = $image;
        if (count($images) >= 20) {
            break;
        }
    }

    $count = count($images);
    if ($count < 3) {
        foreach ($images as $image) {
            imagedestroy($image);
        }
        return false;
    }

    $overlay = imagecreatetruecolor(300, 350);
    imagealphablending($overlay, false);
    imagesavealpha($overlay, true);
    $transparent = imagecolorallocatealpha($overlay, 0, 0, 0, 127);
    imagefilledrectangle($overlay, 0, 0, 300, 350, $transparent);

    for ($y = 0; $y < 350; $y++) {
        for ($x = 0; $x < 300; $x++) {
            $reds = [];
            $greens = [];
            $blues = [];
            $alphas = [];
            $opaque = 0;

            foreach ($images as $image) {
                $color = imagecolorat($image, $x, $y);
                $alpha = ($color >> 24) & 127;
                $alphas[] = $alpha;
                $reds[] = ($color >> 16) & 255;
                $greens[] = ($color >> 8) & 255;
                $blues[] = $color & 255;
                if ($alpha < 100) {
                    $opaque++;
                }
            }

            if ($opaque < $count * 0.75) {
                continue;
            }

            $redSpread = max($reds) - min($reds);
            $greenSpread = max($greens) - min($greens);
            $blueSpread = max($blues) - min($blues);
            $totalSpread = $redSpread + $greenSpread + $blueSpread;
            if ($totalSpread >= 95 && ($redSpread >= 32 || $greenSpread >= 32 || $blueSpread >= 32)) {
                continue;
            }

            sort($reds);
            sort($greens);
            sort($blues);
            sort($alphas);
            $middle = (int)floor($count / 2);
            $color = imagecolorallocatealpha($overlay, $reds[$middle], $greens[$middle], $blues[$middle], $alphas[$middle]);
            imagesetpixel($overlay, $x, $y, $color);
        }
    }

    foreach ($images as $image) {
        imagedestroy($image);
    }

    $saved = imagepng($overlay, $targetPath);
    imagedestroy($overlay);
    if ($saved) {
        @chmod($targetPath, 0644);
    }

    return $saved;
}

function minion_frame_overlay_path(): ?string
{
    $path = absolute_public_path('/uploads/framed/_minion-frame-overlay.png');
    if (is_file($path)) {
        return $path;
    }

    if (build_minion_frame_overlay($path) && is_file($path)) {
        return $path;
    }

    return null;
}

function frame_pixel_is_transparent($image, int $x, int $y): bool
{
    $color = imagecolorat($image, $x, $y);

    return (($color >> 24) & 127) > 90;
}

function frame_outside_mask($frame): array
{
    $width = imagesx($frame);
    $height = imagesy($frame);
    $outside = array_fill(0, $height, array_fill(0, $width, false));
    $queue = [];

    $push = static function (int $x, int $y) use (&$outside, &$queue, $frame, $width, $height): void {
        if ($x < 0 || $x >= $width || $y < 0 || $y >= $height || $outside[$y][$x]) {
            return;
        }
        if (!frame_pixel_is_transparent($frame, $x, $y)) {
            return;
        }
        $outside[$y][$x] = true;
        $queue[] = [$x, $y];
    };

    for ($x = 0; $x < $width; $x++) {
        $push($x, 0);
        $push($x, $height - 1);
    }
    for ($y = 0; $y < $height; $y++) {
        $push(0, $y);
        $push($width - 1, $y);
    }

    for ($index = 0; $index < count($queue); $index++) {
        [$x, $y] = $queue[$index];
        $push($x + 1, $y);
        $push($x - 1, $y);
        $push($x, $y + 1);
        $push($x, $y - 1);
    }

    return $outside;
}

function copy_cover($target, $source, int $dstX, int $dstY, int $dstW, int $dstH): void
{
    $srcW = imagesx($source);
    $srcH = imagesy($source);
    $targetRatio = $dstW / $dstH;
    $sourceRatio = $srcW / $srcH;

    if ($sourceRatio > $targetRatio) {
        $cropH = $srcH;
        $cropW = (int)round($srcH * $targetRatio);
        $srcX = (int)floor(($srcW - $cropW) / 2);
        $srcY = 0;
    } else {
        $cropW = $srcW;
        $cropH = (int)round($srcW / $targetRatio);
        $srcX = 0;
        $srcY = (int)floor(($srcH - $cropH) / 2);
    }

    imagecopyresampled($target, $source, $dstX, $dstY, $srcX, $srcY, $dstW, $dstH, $cropW, $cropH);
}

function cover_crop_geometry(
    int $srcW,
    int $srcH,
    int $dstW,
    int $dstH,
    float $zoom = 1.0,
    float $verticalFocus = 0.5
): array
{
    $targetRatio = $dstW / $dstH;
    $sourceRatio = $srcW / $srcH;

    if ($sourceRatio > $targetRatio) {
        $cropH = $srcH;
        $cropW = (int)round($srcH * $targetRatio);
    } else {
        $cropW = $srcW;
        $cropH = (int)round($srcW / $targetRatio);
    }

    $zoom = max(1.0, $zoom);
    $cropW = max(1, (int)round($cropW / $zoom));
    $cropH = max(1, (int)round($cropH / $zoom));
    $srcX = (int)floor(($srcW - $cropW) / 2);
    $verticalFocus = max(0.0, min(1.0, $verticalFocus));
    $srcY = (int)round(($srcH - $cropH) * $verticalFocus);

    return [$srcX, $srcY, $cropW, $cropH];
}

function copy_cover_zoom(
    $target,
    $source,
    int $dstX,
    int $dstY,
    int $dstW,
    int $dstH,
    float $zoom,
    float $verticalFocus = 0.5
): void
{
    [$srcX, $srcY, $cropW, $cropH] = cover_crop_geometry(
        imagesx($source),
        imagesy($source),
        $dstW,
        $dstH,
        $zoom,
        $verticalFocus
    );

    imagecopyresampled($target, $source, $dstX, $dstY, $srcX, $srcY, $dstW, $dstH, $cropW, $cropH);
}

function mask_spell_frame_art($image): void
{
    $transparent = imagecolorallocatealpha($image, 0, 0, 0, 127);
    imagealphablending($image, false);

    for ($y = 0; $y < 350; $y++) {
        for ($x = 0; $x < 300; $x++) {
            $ellipse = (($x - 150) * ($x - 150) / (80 * 80)) + (($y - 148) * ($y - 148) / (108 * 108)) <= 1.0;
            $upperBody = $x >= 70 && $x <= 230 && $y >= 120 && $y <= 210;
            $lowerBody = $x >= 104 && $x <= 196 && $y >= 210 && $y <= 262;
            if (!$ellipse && !$upperBody && !$lowerBody) {
                imagesetpixel($image, $x, $y, $transparent);
            }
        }
    }

    imagesavealpha($image, true);
}

function mask_ellipse($image, int $cx, int $cy, int $rx, int $ry): void
{
    $transparent = imagecolorallocatealpha($image, 0, 0, 0, 127);
    imagealphablending($image, false);

    $width = imagesx($image);
    $height = imagesy($image);
    for ($y = 0; $y < $height; $y++) {
        for ($x = 0; $x < $width; $x++) {
            $inside = (($x - $cx) * ($x - $cx) / ($rx * $rx)) + (($y - $cy) * ($y - $cy) / ($ry * $ry)) <= 1.0;
            if (!$inside) {
                imagesetpixel($image, $x, $y, $transparent);
            }
        }
    }

    imagesavealpha($image, true);
}

function clear_spell_frame_window($overlay): void
{
    imagealphablending($overlay, false);
    imagesavealpha($overlay, true);
    $transparent = imagecolorallocatealpha($overlay, 0, 0, 0, 127);

    imagefilledellipse($overlay, 150, 148, 152, 208, $transparent);
    imagefilledrectangle($overlay, 84, 120, 216, 210, $transparent);
    imagefilledrectangle($overlay, 104, 210, 196, 265, $transparent);
}

function save_spell_framed_like_minion($artSource, string $targetPath): bool
{
    $overlayPath = minion_frame_overlay_path() ?? spell_frame_template_path();
    if ($overlayPath === null) {
        return false;
    }

    $overlay = @imagecreatefrompng($overlayPath);
    if (!$overlay) {
        return false;
    }
    imagepalettetotruecolor($overlay);
    imagealphablending($overlay, false);
    imagesavealpha($overlay, true);
    $transparent = imagecolorallocatealpha($overlay, 0, 0, 0, 127);
    static $outside = null;
    if (!is_array($outside)) {
        $outside = frame_outside_mask($overlay);
    }

    $artLayer = imagecreatetruecolor(300, 350);
    imagealphablending($artLayer, false);
    imagesavealpha($artLayer, true);
    imagefilledrectangle($artLayer, 0, 0, 300, 350, $transparent);
    // Fill the native frame window without changing the artwork's aspect
    // ratio. Extra zoom made large subjects look horizontally inflated and
    // cropped heads; the upper focal point only controls unavoidable cover
    // cropping for unusually tall sources.
    copy_cover_zoom($artLayer, $artSource, 48, 30, 204, 270, 1.0, 0.25);
    for ($y = 0; $y < 350; $y++) {
        for ($x = 0; $x < 300; $x++) {
            if (!frame_pixel_is_transparent($overlay, $x, $y) || $outside[$y][$x]) {
                imagesetpixel($artLayer, $x, $y, $transparent);
            }
        }
    }
    imagesavealpha($artLayer, true);

    $canvas = imagecreatetruecolor(300, 350);
    imagealphablending($canvas, false);
    imagesavealpha($canvas, true);
    imagefilledrectangle($canvas, 0, 0, 300, 350, $transparent);
    imagecopy($canvas, $artLayer, 0, 0, 0, 0, 300, 350);
    imagealphablending($canvas, true);
    imagecopy($canvas, $overlay, 0, 0, 0, 0, 300, 350);
    imagesavealpha($canvas, true);

    $temporaryPath = tempnam(dirname($targetPath), '.framed-');
    $saved = $temporaryPath !== false && imagepng($canvas, $temporaryPath);
    if ($saved) {
        @chmod($temporaryPath, 0644);
        $saved = @rename($temporaryPath, $targetPath);
    }
    if (!$saved && $temporaryPath !== false && is_file($temporaryPath)) {
        @unlink($temporaryPath);
    }

    imagedestroy($artLayer);
    imagedestroy($overlay);
    imagedestroy($canvas);

    return $saved;
}

function framed_render_recipe_mtime(): int
{
    static $mtime = null;
    if (is_int($mtime)) {
        return $mtime;
    }

    $marker = absolute_public_path('/uploads/framed/.render-recipe-' . FRAMED_RENDER_RECIPE_VERSION);
    ensure_parent_dir($marker);
    if (!is_file($marker)) {
        file_put_contents($marker, FRAMED_RENDER_RECIPE_VERSION . PHP_EOL, LOCK_EX);
        @chmod($marker, 0644);
    }
    $mtime = (int)(filemtime($marker) ?: time());

    return $mtime;
}

function framed_render_needs_refresh(string $path): bool
{
    return !is_file($path)
        || filesize($path) <= 0
        || (int)filemtime($path) < framed_render_recipe_mtime();
}

/**
 * Materializes the compact card render, square art and 300x350 portrait frame.
 *
 * Active minions use the same local asset contract as Tavern spells. This is
 * important because a full 512x776 card render is not a framed portrait even
 * though both files are PNG images.
 */
function save_local_card_assets(string $cardId, array $cardUrls = [], array $artUrls = []): array
{
    $paths = [
        'card_image' => public_upload_path('cards', $cardId, 'png'),
        'art_image' => public_upload_path('art', $cardId, 'jpg'),
        'framed_image' => public_upload_path('framed', $cardId, 'png'),
    ];

    $absolute = array_map('absolute_public_path', $paths);
    $allExist = true;
    foreach ($absolute as $asset => $path) {
        if (!is_file($path)
            || filesize($path) <= 0
            || ($asset === 'framed_image' && framed_render_needs_refresh($path))) {
            $allExist = false;
            break;
        }
    }
    if ($allExist) {
        return $paths;
    }

    foreach ($absolute as $path) {
        ensure_parent_dir($path);
    }

    $encodedId = rawurlencode($cardId);
    // HearthstoneJSON publishes golden Battlegrounds renders under the
    // `_G_triple.png` filename. The plain `_G.png` URL returns 404, so prefer
    // the triple render while retaining the ordinary candidates as fallbacks.
    if (preg_match('/_Gt?$/', $cardId) === 1) {
        array_unshift($cardUrls, HSJ_BGS_RENDER_BASE . $encodedId . '_triple.png');
    }
    $cardUrls[] = HSJ_BGS_RENDER_BASE . $encodedId . '.png';
    $cardUrls[] = HSJ_RENDER_BASE . $encodedId . '.png';
    $cardImage = null;
    if (!is_file($absolute['card_image'])) {
        foreach (array_unique(array_filter($cardUrls)) as $url) {
            $cardImage = image_from_bytes(fetch_binary($url));
            if ($cardImage) {
                break;
            }
        }
    }
    if ($cardImage) {
        $cardSmall = resize_exact($cardImage, 256, 388);
        imagepng($cardSmall, $absolute['card_image']);
        @chmod($absolute['card_image'], 0644);
        imagedestroy($cardSmall);
    }

    // The original HearthstoneJSON artwork is canonical. Caller-provided URLs
    // can point to complete card renders, so they are fallbacks only.
    $artUrls = array_values(array_unique(array_filter(array_merge(
        [HSJ_ORIG_BASE . $encodedId . '.png'],
        $artUrls
    ))));
    $hasLocalArt = is_file($absolute['art_image']) && filesize($absolute['art_image']) > 0;
    $localArtImage = $hasLocalArt ? @imagecreatefromjpeg($absolute['art_image']) : null;
    $sourceArtImage = null;

    // A recipe refresh must start from the upstream original. Reusing the
    // previous 512x512 derivative made the pipeline non-idempotent: portrait
    // artwork was widened once and every later refresh preserved that damage.
    if (framed_render_needs_refresh($absolute['framed_image']) || !$localArtImage) {
        foreach ($artUrls as $url) {
            $sourceArtImage = image_from_bytes(fetch_binary($url));
            if ($sourceArtImage) {
                break;
            }
        }
    }

    $artImage = $sourceArtImage ?: $localArtImage;
    if (!$artImage && !$cardImage && is_file($absolute['card_image'])) {
        $cardImage = @imagecreatefrompng($absolute['card_image']);
    }
    if (!$artImage && $cardImage) {
        $artImage = crop_image(
            $cardImage,
            (int)round(imagesx($cardImage) * 0.16),
            (int)round(imagesy($cardImage) * 0.13),
            (int)round(imagesx($cardImage) * 0.68),
            (int)round(imagesy($cardImage) * 0.34)
        );
    }
    if ($artImage) {
        $trimmedArt = trim_light_border($artImage);

        // Repair only the legacy square derivative when the canonical source
        // is demonstrably portrait. Genuine square artwork remains square and
        // higher-resolution Wiki full art is left untouched.
        $repairDistortedLocalArt = false;
        if ($localArtImage && $sourceArtImage && imagesx($localArtImage) === 512 && imagesy($localArtImage) === 512) {
            $trimmedRatio = imagesx($trimmedArt) / max(1, imagesy($trimmedArt));
            $repairDistortedLocalArt = abs($trimmedRatio - 1.0) > 0.02;
        }
        if (!$hasLocalArt || $repairDistortedLocalArt) {
            $art = resize_within($trimmedArt, 512, 512);
            imagejpeg($art, $absolute['art_image'], 92);
            @chmod($absolute['art_image'], 0644);
            imagedestroy($art);
        }
        save_spell_framed_like_minion($trimmedArt, $absolute['framed_image']);
        if ($trimmedArt !== $artImage) {
            imagedestroy($trimmedArt);
        }
        if ($sourceArtImage) {
            imagedestroy($sourceArtImage);
        }
        if ($localArtImage) {
            imagedestroy($localArtImage);
        }
        if (!$sourceArtImage && !$localArtImage) {
            imagedestroy($artImage);
        }
    } elseif ($cardImage) {
        $fallback = crop_image(
            $cardImage,
            (int)round(imagesx($cardImage) * 0.16),
            (int)round(imagesy($cardImage) * 0.13),
            (int)round(imagesx($cardImage) * 0.68),
            (int)round(imagesy($cardImage) * 0.34)
        );
        save_spell_framed_like_minion($fallback, $absolute['framed_image']);
        imagedestroy($fallback);
    }
    if ($cardImage) {
        imagedestroy($cardImage);
    }

    return [
        'card_image' => is_file($absolute['card_image']) ? $paths['card_image'] : HSJ_BGS_RENDER_BASE . $encodedId . '.png',
        'art_image' => is_file($absolute['art_image']) ? $paths['art_image'] : HSJ_ORIG_BASE . $encodedId . '.png',
        // A normal card render must never masquerade as a framed portrait.
        'framed_image' => is_file($absolute['framed_image']) ? $paths['framed_image'] : null,
    ];
}

function int_or_null($value): ?int
{
    if ($value === null || $value === '') {
        return null;
    }

    return (int)$value;
}

function map_creature_type(array $card): ?string
{
    $races = [];
    if (!empty($card['race'])) {
        $races[] = $card['race'];
    }
    if (!empty($card['races']) && is_array($card['races'])) {
        $races = array_merge($races, $card['races']);
    }

    $map = [
        'ALL' => 'all',
        'UNDEAD' => 'undead',
        'DRAGON' => 'dragon',
        'MECHANICAL' => 'mech',
        'MECH' => 'mech',
        'MURLOC' => 'murloc',
        'DEMON' => 'demon',
        'QUILBOAR' => 'quilboar',
        'NAGA' => 'naga',
        'PIRATE' => 'pirate',
        'BEAST' => 'beast',
        'ELEMENTAL' => 'elemental',
    ];

    foreach ($races as $race) {
        $race = strtoupper((string)$race);
        if (isset($map[$race])) {
            return $map[$race];
        }
    }

    return null;
}

function build_notes(array $ru, array $en): ?string
{
    $parts = [];
    $text = clean_text($ru['text'] ?? null);
    if ($text !== null) {
        $parts[] = $text;
    }

    $mechanics = [];
    foreach ([$ru['mechanics'] ?? [], $ru['referencedTags'] ?? []] as $list) {
        if (!is_array($list)) {
            continue;
        }
        foreach ($list as $mechanic) {
            $mechanic = trim((string)$mechanic);
            if ($mechanic !== '') {
                $mechanics[$mechanic] = true;
            }
        }
    }
    if ($mechanics) {
        $parts[] = 'Механики: ' . implode(', ', array_keys($mechanics));
    }

    $enText = clean_text($en['text'] ?? null);
    if ($enText !== null) {
        $parts[] = 'EN: ' . $enText;
    }

    return $parts ? implode("\n", $parts) : null;
}

function is_duos_only_battleground_card(array $card): int
{
    $id = (string)($card['id'] ?? '');
    if (strpos($id, 'BGDUO') === 0) {
        return 1;
    }

    $dbf = int_or_null($card['dbfId'] ?? null);
    if ($dbf !== null && in_array($dbf, [117671, 119470, 119471], true)) {
        return 1;
    }

    return 0;
}

function normalize_card(array $ru, array $en = []): ?array
{
    if (($ru['set'] ?? null) !== 'BATTLEGROUNDS') {
        return null;
    }

    $type = (string)($ru['type'] ?? '');
    if (!in_array($type, ['MINION', 'SPELL', 'BATTLEGROUND_SPELL'], true)) {
        return null;
    }
    if (empty($ru['id'])) {
        return null;
    }

    $cardType = $type === 'MINION' ? 'minion' : 'spell';
    if ($cardType === 'spell') {
        if ($type !== 'BATTLEGROUND_SPELL') {
            return null;
        }
        if (($ru['spellSchool'] ?? null) !== 'TAVERN') {
            return null;
        }
    }
    $isPoolKey = $cardType === 'spell' ? 'isBattlegroundsPoolSpell' : 'isBattlegroundsPoolMinion';
    $inPool = !empty($ru[$isPoolKey]) ? 1 : 0;
    $cardId = (string)$ru['id'];
    $imagePaths = save_local_card_assets($cardId);
    $baseDbf = int_or_null($ru['battlegroundsNormalDbfId'] ?? $en['battlegroundsNormalDbfId'] ?? null);
    $premiumDbf = int_or_null($ru['battlegroundsPremiumDbfId'] ?? $en['battlegroundsPremiumDbfId'] ?? null);

    $payload = [
        'card_type' => $cardType,
        'variant_kind' => $baseDbf !== null ? 'golden' : 'base',
        'base_dbf' => $baseDbf,
        'base_card_id' => null,
        'premium_dbf' => $premiumDbf,
        'name' => (string)($ru['name'] ?? $en['name'] ?? $cardId),
        'name_en' => (string)($en['name'] ?? $ru['name'] ?? $cardId),
        'card_id' => $cardId,
        'dbf' => int_or_null($ru['dbfId'] ?? $en['dbfId'] ?? null),
        'tavern_tier' => int_or_null($ru['techLevel'] ?? $en['techLevel'] ?? null),
        'creature_type' => $cardType === 'minion' ? map_creature_type($ru) : null,
        'attack' => $cardType === 'minion' ? int_or_null($ru['attack'] ?? null) : null,
        'health' => $cardType === 'minion' ? int_or_null($ru['health'] ?? null) : null,
        'in_pool' => $inPool,
        'duos_only' => is_duos_only_battleground_card($ru),
        'card_image' => $imagePaths['card_image'],
        'art_image' => $imagePaths['art_image'],
        'framed_image' => $imagePaths['framed_image'],
        'notes' => build_notes($ru, $en),
        'source' => SOURCE_HSJ,
        'source_payload' => [
            'ru' => array_intersect_key($ru, array_flip([
                'id', 'dbfId', 'name', 'text', 'set', 'type', 'techLevel', 'attack', 'health',
                'cost', 'race', 'races', 'mechanics', 'referencedTags', 'spellSchool',
                'isBattlegroundsPoolMinion', 'isBattlegroundsPoolSpell',
                'battlegroundsNormalDbfId', 'battlegroundsPremiumDbfId',
            ])),
            'en' => array_intersect_key($en, array_flip([
                'id', 'dbfId', 'name', 'text', 'set', 'type', 'techLevel', 'attack', 'health',
                'cost', 'race', 'races', 'mechanics', 'referencedTags', 'spellSchool',
                'isBattlegroundsPoolMinion', 'isBattlegroundsPoolSpell',
                'battlegroundsNormalDbfId', 'battlegroundsPremiumDbfId',
            ])),
        ],
    ];
    $payload['source_hash'] = hash('sha256', json_encode($payload['source_payload'], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));

    return $payload;
}

function should_replace_image(?string $current): bool
{
    $current = trim((string)$current);

    return $current === '' || preg_match('~^https?://~i', $current) === 1;
}

function record_change(PDO $pdo, string $cardId, string $source, ?string $oldHash, string $newHash, string $type, array $payload): void
{
    $stmt = $pdo->prepare("
        INSERT INTO battlegrounds_card_changes (card_id, source, old_hash, new_hash, change_type, payload_json)
        VALUES (:card_id, :source, :old_hash, :new_hash, :change_type, :payload_json)
    ");
    $stmt->execute([
        'card_id' => $cardId,
        'source' => $source,
        'old_hash' => $oldHash,
        'new_hash' => $newHash,
        'change_type' => $type,
        'payload_json' => json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
    ]);
}

function upsert_card(PDO $pdo, array $card, bool $dryRun = false): array
{
    $select = $pdo->prepare('SELECT * FROM battlegrounds_cards WHERE card_id = :card_id LIMIT 1');
    $select->execute(['card_id' => $card['card_id']]);
    $existing = $select->fetch();

    if (!$existing) {
        if (!$dryRun) {
            $stmt = $pdo->prepare("
                INSERT INTO battlegrounds_cards (
                    name, name_en, card_id, dbf, card_type, variant_kind, base_dbf, base_card_id, premium_dbf,
                    tavern_tier, creature_type, attack, health,
                    in_pool, duos_only, card_image, art_image, framed_image, notes,
                    source, source_hash, first_seen_at, last_seen_at, changed_at
                ) VALUES (
                    :name, :name_en, :card_id, :dbf, :card_type, :variant_kind, :base_dbf, :base_card_id, :premium_dbf,
                    :tavern_tier, :creature_type, :attack, :health,
                    :in_pool, :duos_only, :card_image, :art_image, :framed_image, :notes,
                    :source, :source_hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            ");
            $stmt->execute([
                'name' => $card['name'],
                'name_en' => $card['name_en'],
                'card_id' => $card['card_id'],
                'dbf' => $card['dbf'],
                'card_type' => $card['card_type'],
                'variant_kind' => $card['variant_kind'],
                'base_dbf' => $card['base_dbf'],
                'base_card_id' => $card['base_card_id'],
                'premium_dbf' => $card['premium_dbf'],
                'tavern_tier' => $card['tavern_tier'],
                'creature_type' => $card['creature_type'],
                'attack' => $card['attack'],
                'health' => $card['health'],
                'in_pool' => $card['in_pool'],
                'duos_only' => $card['duos_only'],
                'card_image' => $card['card_image'],
                'art_image' => $card['art_image'],
                'framed_image' => $card['framed_image'],
                'notes' => $card['notes'],
                'source' => $card['source'],
                'source_hash' => $card['source_hash'],
            ]);
            record_change($pdo, $card['card_id'], $card['source'], null, $card['source_hash'], 'new', $card['source_payload']);
        }

        return ['inserted' => 1, 'updated' => 0, 'changed' => 1];
    }

    $changed = ($existing['source_hash'] ?? null) !== $card['source_hash'];
    $params = [
        'id' => (int)$existing['id'],
        'name' => $card['name'],
        'name_en' => $card['name_en'],
        'dbf' => $card['dbf'],
        'card_type' => $card['card_type'],
        'variant_kind' => $card['variant_kind'],
        'base_dbf' => $card['base_dbf'],
        'base_card_id' => $card['base_card_id'],
        'premium_dbf' => $card['premium_dbf'],
        'tavern_tier' => $card['tavern_tier'],
        'creature_type' => $card['creature_type'],
        'attack' => $card['attack'],
        'health' => $card['health'],
        'in_pool' => $card['in_pool'],
        'duos_only' => $card['duos_only'],
        'card_image' => should_replace_image($existing['card_image'] ?? null) ? $card['card_image'] : $existing['card_image'],
        'art_image' => should_replace_image($existing['art_image'] ?? null) ? $card['art_image'] : $existing['art_image'],
        'framed_image' => should_replace_image($existing['framed_image'] ?? null) ? $card['framed_image'] : $existing['framed_image'],
        'notes' => $card['notes'],
        'source' => $card['source'],
        'source_hash' => $card['source_hash'],
    ];

    if (!$dryRun) {
        $stmt = $pdo->prepare("
            UPDATE battlegrounds_cards
            SET name = :name,
                name_en = :name_en,
                dbf = :dbf,
                card_type = :card_type,
                variant_kind = :variant_kind,
                base_dbf = :base_dbf,
                base_card_id = :base_card_id,
                premium_dbf = :premium_dbf,
                tavern_tier = :tavern_tier,
                creature_type = :creature_type,
                attack = :attack,
                health = :health,
                in_pool = :in_pool,
                duos_only = :duos_only,
                card_image = :card_image,
                art_image = :art_image,
                framed_image = :framed_image,
                notes = :notes,
                source = :source,
                source_hash = :source_hash,
                first_seen_at = COALESCE(first_seen_at, CURRENT_TIMESTAMP),
                last_seen_at = CURRENT_TIMESTAMP,
                changed_at = IF(:was_changed = 1, CURRENT_TIMESTAMP, changed_at)
            WHERE id = :id
        ");
        $stmt->execute($params + ['was_changed' => $changed ? 1 : 0]);
        if ($changed) {
            record_change($pdo, $card['card_id'], $card['source'], $existing['source_hash'] ?? null, $card['source_hash'], 'changed', $card['source_payload']);
        }
    }

    return ['inserted' => 0, 'updated' => 1, 'changed' => $changed ? 1 : 0];
}

function scan_hearthstonejson(PDO $pdo, bool $dryRun = false): array
{
    $ruCards = fetch_json(HSJ_RU_URL);
    $enCards = fetch_json(HSJ_EN_URL);
    $enById = [];
    foreach ($enCards as $card) {
        if (is_array($card) && !empty($card['id'])) {
            $enById[(string)$card['id']] = $card;
        }
    }

    $stats = ['scanned' => 0, 'inserted' => 0, 'updated' => 0, 'changed' => 0];
    foreach ($ruCards as $ru) {
        if (!is_array($ru) || empty($ru['id'])) {
            continue;
        }
        $normalized = normalize_card($ru, $enById[(string)$ru['id']] ?? []);
        if ($normalized === null) {
            continue;
        }

        $stats['scanned']++;
        $result = upsert_card($pdo, $normalized, $dryRun);
        foreach (['inserted', 'updated', 'changed'] as $key) {
            $stats[$key] += $result[$key];
        }
    }

    if (!$dryRun) {
        $pdo->exec("
            UPDATE battlegrounds_cards premium
            LEFT JOIN battlegrounds_cards base_card ON base_card.dbf = premium.base_dbf
            SET premium.base_card_id = base_card.card_id
            WHERE premium.variant_kind = 'golden'
        ");
        $pdo->exec("
            UPDATE battlegrounds_cards base_card
            INNER JOIN battlegrounds_cards premium
                ON premium.base_dbf = base_card.dbf
               AND premium.variant_kind = 'golden'
            SET base_card.golden_image = premium.card_image
            WHERE premium.card_image IS NOT NULL AND premium.card_image <> ''
        ");
    }

    return $stats;
}

function blizzard_credentials(): ?array
{
    $clientId = getenv('BLIZZARD_CLIENT_ID') ?: '';
    $clientSecret = getenv('BLIZZARD_CLIENT_SECRET') ?: '';
    $region = getenv('BLIZZARD_REGION') ?: 'us';
    $locale = getenv('BLIZZARD_LOCALE') ?: 'ru_RU';
    if ($clientId === '' || $clientSecret === '') {
        return null;
    }

    return [$clientId, $clientSecret, $region, $locale];
}

function normalize_blizzard_card(array $card): ?array
{
    $rawId = $card['id'] ?? null;
    if ($rawId === null && isset($card['slug']) && preg_match('/^\d+/', (string)$card['slug'], $matches)) {
        $rawId = $matches[0];
    }
    if ($rawId === null || $rawId === '') {
        return null;
    }

    $dbf = (int)$rawId;
    $cardId = 'blizzard:' . $dbf;
    if (isset($card['cardSetId']) && (int)$card['cardSetId'] !== 1453) {
        return null;
    }

    $typeSlug = strtolower((string)($card['cardType']['slug'] ?? $card['cardTypeSlug'] ?? ''));
    if ($typeSlug === '') {
        $typeSlug = ((int)($card['cardTypeId'] ?? 0)) === 5 ? 'spell' : (((int)($card['cardTypeId'] ?? 0)) === 4 ? 'minion' : '');
    }
    if (!in_array($typeSlug, ['minion', 'spell'], true)) {
        return null;
    }
    if ($typeSlug === 'spell') {
        return null;
    }

    $battlegrounds = is_array($card['battlegrounds'] ?? null) ? $card['battlegrounds'] : [];
    $image = (string)($battlegrounds['image'] ?? $card['image'] ?? '');
    $imageGold = (string)($battlegrounds['imageGold'] ?? $card['imageGold'] ?? '');
    $payload = [
        'id' => $card['id'] ?? null,
        'slug' => $card['slug'] ?? null,
        'name' => $card['name'] ?? null,
        'text' => $card['text'] ?? null,
        'cardType' => $card['cardType'] ?? null,
        'minionType' => $card['minionType'] ?? null,
        'battlegrounds' => $battlegrounds,
    ];

    return [
        'card_id' => $cardId,
        'dbf' => $dbf,
        'card_type' => $typeSlug,
        'name' => (string)($card['name'] ?? $cardId),
        'image' => $image !== '' ? $image : null,
        'golden_image' => $imageGold !== '' ? $imageGold : null,
        'source_payload' => $payload,
        'source_hash' => hash('sha256', json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)),
    ];
}

function update_blizzard_card(PDO $pdo, array $card, bool $dryRun = false): array
{
    $select = $pdo->prepare('SELECT * FROM battlegrounds_cards WHERE card_id = :card_id OR dbf = :dbf ORDER BY card_id = :sort_card_id DESC LIMIT 1');
    $select->execute(['card_id' => $card['card_id'], 'sort_card_id' => $card['card_id'], 'dbf' => $card['dbf']]);
    $existing = $select->fetch();
    if (!$existing) {
        $newImages = $card['card_type'] === 'spell'
            ? save_local_card_assets($card['card_id'], array_filter([$card['image']]), array_filter([$card['image']]))
            : [
                'card_image' => $card['image'],
                'art_image' => null,
                'framed_image' => null,
            ];
        if (!$dryRun) {
            $stmt = $pdo->prepare("
                INSERT INTO battlegrounds_cards (
                    name, card_id, dbf, card_type, in_pool, card_image, golden_image, art_image, framed_image,
                    notes, source, source_hash, first_seen_at, last_seen_at, changed_at
                ) VALUES (
                    :name, :card_id, :dbf, :card_type, 0, :card_image, :golden_image, :art_image, :framed_image,
                    :notes, :source, :source_hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            ");
            $stmt->execute([
                'name' => $card['name'],
                'card_id' => $card['card_id'],
                'dbf' => $card['dbf'],
                'card_type' => $card['card_type'],
                'card_image' => $newImages['card_image'],
                'golden_image' => $card['golden_image'],
                'art_image' => $newImages['art_image'],
                'framed_image' => $newImages['framed_image'],
                'notes' => 'Импортировано из Blizzard API. Пул уточняется по HearthstoneJSON.',
                'source' => SOURCE_BLIZZARD,
                'source_hash' => $card['source_hash'],
            ]);
            record_change($pdo, $card['card_id'], SOURCE_BLIZZARD, null, $card['source_hash'], 'new', $card['source_payload']);
        }

        return ['inserted' => 1, 'updated' => 0, 'changed' => 1];
    }

    $changed = ($existing['source'] ?? null) === SOURCE_BLIZZARD && ($existing['source_hash'] ?? null) !== $card['source_hash'];
    $imagePaths = $card['card_type'] === 'spell'
        ? save_local_card_assets((string)$existing['card_id'], array_filter([$card['image']]), array_filter([$card['image']]))
        : [
            'card_image' => $card['image'],
            'art_image' => $existing['art_image'] ?? null,
            'framed_image' => null,
        ];
    if (!$dryRun) {
        $stmt = $pdo->prepare("
            UPDATE battlegrounds_cards
            SET card_image = :card_image,
                golden_image = :golden_image,
                art_image = :art_image,
                framed_image = :framed_image,
                last_seen_at = CURRENT_TIMESTAMP,
                changed_at = IF(:was_changed = 1, CURRENT_TIMESTAMP, changed_at)
            WHERE id = :id
        ");
        $stmt->execute([
            'id' => (int)$existing['id'],
            'card_image' => should_replace_image($existing['card_image'] ?? null) ? $imagePaths['card_image'] : $existing['card_image'],
            'golden_image' => should_replace_image($existing['golden_image'] ?? null) ? $card['golden_image'] : $existing['golden_image'],
            'art_image' => should_replace_image($existing['art_image'] ?? null) ? $imagePaths['art_image'] : $existing['art_image'],
            'framed_image' => should_replace_image($existing['framed_image'] ?? null) ? $imagePaths['framed_image'] : $existing['framed_image'],
            'was_changed' => $changed ? 1 : 0,
        ]);
        if ($changed) {
            record_change($pdo, $card['card_id'], SOURCE_BLIZZARD, $existing['source_hash'] ?? null, $card['source_hash'], 'changed', $card['source_payload']);
        }
    }

    return ['inserted' => 0, 'updated' => 1, 'changed' => $changed ? 1 : 0];
}

function scan_blizzard(PDO $pdo, bool $dryRun = false): array
{
    $credentials = blizzard_credentials();
    if ($credentials === null) {
        return ['scanned' => 0, 'inserted' => 0, 'updated' => 0, 'changed' => 0, 'skipped' => 1];
    }

    [$clientId, $clientSecret, $region, $locale] = $credentials;
    $tokenUrl = 'https://oauth.battle.net/token';
    $token = fetch_text_post($tokenUrl, ['grant_type' => 'client_credentials'], [
        'Authorization: Basic ' . base64_encode($clientId . ':' . $clientSecret),
    ]);
    if (empty($token['access_token'])) {
        throw new RuntimeException('Blizzard token response does not contain access_token.');
    }

    $stats = ['scanned' => 0, 'inserted' => 0, 'updated' => 0, 'changed' => 0];
    $page = 1;
    do {
        $url = 'https://' . $region . '.api.blizzard.com/hearthstone/cards?'
            . http_build_query([
                'locale' => $locale,
                'gameMode' => 'battlegrounds',
                'pageSize' => 500,
                'page' => $page,
            ]);
        $response = fetch_json($url, [
            'Authorization: Bearer ' . $token['access_token'],
        ]);
        $cards = $response['cards'] ?? [];
        if (!is_array($cards)) {
            break;
        }
        foreach ($cards as $card) {
            if (!is_array($card)) {
                continue;
            }
            $normalized = normalize_blizzard_card($card);
            if ($normalized === null) {
                continue;
            }
            $stats['scanned']++;
            $result = update_blizzard_card($pdo, $normalized, $dryRun);
            foreach (['inserted', 'updated', 'changed'] as $key) {
                $stats[$key] += $result[$key];
            }
        }
        $pageCount = (int)($response['pageCount'] ?? $page);
        $page++;
    } while ($page <= $pageCount);

    return $stats;
}

function run_framing_self_test(): void
{
    foreach ([[377, 512], [512, 512], [1530, 2048]] as [$srcW, $srcH]) {
        [, , $cropW, $cropH] = cover_crop_geometry($srcW, $srcH, 204, 270, 1.0, 0.25);
        $scaleX = 204 / $cropW;
        $scaleY = 270 / $cropH;
        $relativeError = abs($scaleX - $scaleY) / max($scaleX, $scaleY);
        if ($relativeError > 0.01) {
            throw new RuntimeException("Framed crop changes aspect ratio for {$srcW}x{$srcH}.");
        }
        if ($cropW < $srcW && $cropH < $srcH) {
            throw new RuntimeException("Zoom was applied to {$srcW}x{$srcH} despite the 1.0 recipe.");
        }
    }

    $sample = imagecreatetruecolor(377, 512);
    $resized = resize_within($sample, 512, 512);
    if (imagesx($resized) !== 377 || imagesy($resized) !== 512) {
        throw new RuntimeException('Aspect-preserving art storage regression.');
    }
    imagedestroy($resized);
    imagedestroy($sample);
    echo "framing-self-test: ok\n";
}

$source = cli_option('source', 'all');
$dryRun = cli_option('dry-run', '0') === '1';
if (cli_option('self-test-framing', '0') === '1') {
    run_framing_self_test();
    exit(0);
}
if (!in_array($source, ['all', SOURCE_HSJ, SOURCE_BLIZZARD], true)) {
    fwrite(STDERR, "Unknown source. Use --source=all, --source=hearthstonejson or --source=blizzard.\n");
    exit(2);
}

$pdo = db($config);
ensure_schema($pdo);

$jobs = [];
if ($source === 'all' || $source === SOURCE_HSJ) {
    $jobs[] = SOURCE_HSJ;
}
if ($source === 'all' || $source === SOURCE_BLIZZARD) {
    $jobs[] = SOURCE_BLIZZARD;
}

foreach ($jobs as $job) {
    $runId = start_run($pdo, $job);
    try {
        $stats = $job === SOURCE_HSJ ? scan_hearthstonejson($pdo, $dryRun) : scan_blizzard($pdo, $dryRun);
        $status = !empty($stats['skipped']) ? 'skipped' : 'ok';
        finish_run($pdo, $runId, $stats, $status);
        echo $job . ': ' . json_encode($stats, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
    } catch (Throwable $e) {
        finish_run($pdo, $runId, ['scanned' => 0, 'inserted' => 0, 'updated' => 0, 'changed' => 0], 'error', $e->getMessage());
        fwrite(STDERR, $job . ': ' . $e->getMessage() . PHP_EOL);
        exit(1);
    }
}
