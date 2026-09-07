<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/panel_assets.php';
foreach (panel_script_assets($action) as $panelScript): ?>
    <script src="<?= h($panelScript) ?>" defer></script>
<?php endforeach; ?>
