=== Hearthstone Parses - Interactive Graphs ===
Contributors: Hearthstone Parses Team
Tags: hearthstone, vicious syndicate, hsguru, graphs, radar, scatter plot, shortcode, interactive, canvas
Requires at least: 6.0
Tested up to: 6.5
Stable tag: 1.1.10
License: MIT
License URI: https://opensource.org/licenses/MIT

Integrates highly optimized interactive charts (such as HSGuru Meta Scatter Plot and Vicious Syndicate Radar Networks) into your WordPress website using safe, high-performance shortcodes. Compatible with premium themes like Blocksy and Newspaper.

== Description ==

This plugin provides shortcodes to integrate professional interactive Hearthstone data visualizations powered by the Hearthstone Parses API:

1. **[hs_meta_scatter_standard]** - Displays an interactive HTML5 Canvas scatter plot representing the distribution of Standard meta archetypes based on their **Winrate** (X-axis) and **Popularity** (Y-axis), with active rank switching (Top Legend, Top 5k, Legend, Diamond 4-1).
2. **[hs_meta_scatter_wild]** - Displays an interactive HTML5 Canvas scatter plot representing the distribution of Wild meta archetypes based on their **Winrate** (X-axis) and **Popularity** (Y-axis), with active rank switching.
3. **[hs_vs_radar_chart]** - Displays an interactive force-directed network graph of card synergies matching Vicious Syndicate. It allows selecting classes, choosing specific archetypes, searching for individual cards, and viewing strongest synergy ties. Supports locking to a single class (e.g. Druid) with archetype filters!

Both shortcodes only load their JavaScript and CSS assets on pages where they are actually placed, preserving website speed and performance on other pages.

== Admin Menu ==

This plugin adds a helpful guide page under **Tools** (Инструменты -> Графики Hearthstone) with copy-to-clipboard buttons and full parameters reference.

== Installation ==

1. Upload the `hs-parses-graphs-wp` folder to the `/wp-content/plugins/` directory.
2. Activate the plugin through the 'Plugins' menu in WordPress.
3. Place shortcodes on any page, post, or widget area.

== Shortcodes & Attributes ==

=== 1. Standard Meta Scatter Chart ===
`[hs_meta_scatter_standard]`

*   `rank` (string) - Starting rank. Defaults to `diamond_4to1`. Valid options: `top_legend`, `top_5k`, `legend`, `diamond_4to1`.
*   `show_selector` (string) - Show buttons to toggle rank. Defaults to `yes`. Valid options: `yes`, `no`.
*   `api_url` (string) - The Hearthstone Parses API URL. Defaults to `https://api.kolodahearthstone.com`.
*   `archetypes_api_url` (string) - Public Deckview API used for Russian archetype names. Defaults to `https://api.blizzcore.ru`.
*   `width` (integer) - Width of the canvas. Defaults to `850`.
*   `height` (integer) - Height of the canvas. Defaults to `500`.

*Example:*
`[hs_meta_scatter_standard rank="top_legend" show_selector="yes"]`

=== 2. Wild Meta Scatter Chart ===
`[hs_meta_scatter_wild]`

*   `rank` (string) - Starting rank. Defaults to `legend`. Valid options: `top_legend`, `top_5k`, `legend`, `diamond_4to1`.
*   `show_selector` (string) - Show buttons to toggle rank. Defaults to `yes`. Valid options: `yes`, `no`.
*   `api_url` (string) - The Hearthstone Parses API URL. Defaults to `https://api.kolodahearthstone.com`.
*   `archetypes_api_url` (string) - Public Deckview API used for Russian archetype names. Defaults to `https://api.blizzcore.ru`.

*Example:*
`[hs_meta_scatter_wild rank="legend"]`

=== 3. Vicious Syndicate Radar Chart ===
`[hs_vs_radar_chart]`

*   `class` (string) - Pre-select or lock to a specific class (e.g., `Druid`, `Hunter`, `Mage`, `Paladin`, `Priest`, `Rogue`, `Shaman`, `Warlock`, `Warrior`, `DeathKnight`, `DemonHunter`).
*   `lock_class` (string) - Hide class tabs and lock to the specified class. Defaults to `no`. Valid options: `yes`, `no`.
*   `api_url` (string) - The Hearthstone Parses API URL. Defaults to `https://api.kolodahearthstone.com`.
*   `archetypes_api_url` (string) - Public Deckview API used for Russian archetype names. Defaults to `https://api.blizzcore.ru`.
*   `width` (integer) - Maximum width of the canvas. Defaults to `750`.
*   `height` (integer) - Maximum height of the canvas. Defaults to `750`.

*Example (Lock to Druid showing only Druid's class radar and Druid's archetypes):*
`[hs_vs_radar_chart class="Druid" lock_class="yes"]`

== Theme Compatibility ==

Fully tested and optimized for perfect layout compatibility with:
*   **Blocksy** theme (respects color variables, fonts, and borders).
*   **Newspaper** theme (beautifully scales inside sidebar columns or page builders).

== Changelog ==

= 1.1.10 =
* Bumped the browser card-translation cache namespace after fuzzy matching changes.

= 1.1.9 =
* Added fuzzy card matching for truncated Vicious Syndicate names.
* Improved duplicate HearthstoneJSON card selection to avoid enchantment/token records.

= 1.1.8 =
* Tuned the Vicious Syndicate radar width and canvas aspect ratio for wide pages.

= 1.1.7 =
* Fixed Vicious Syndicate radar overflow inside narrow article columns.
* Added hs-tooltip-compatible card hover targets to the radar table and details panel.

= 1.1.6 =
* Added Escape handling for fullscreen fallback mode.

= 1.1.5 =
* Switched scoped card translation requests to POST to avoid query-string cache issues.

= 1.1.4 =
* Reduced Vicious Syndicate card translation payloads to the cards used by the selected radar.

= 1.1.3 =
* Added fullscreen controls for scatter and Vicious Syndicate radar charts.
* Fixed scatter rank switching by removing stale canvas replacement.
* Added cached Russian archetype names through the Deckview public API.
* Added cached Russian card names for Vicious Syndicate radars through HearthstoneJSON.
* Improved HiDPI canvas rendering, label density, and radar layout.

= 1.1.2 =
* Fixed blank canvas rendering after listener reset for radar and scatter charts.
* Bumped asset version to force browser cache refresh.

= 1.1.1 =
* Fixed Vicious Syndicate radar loading for the current Hearthstone Parses API response shape.
* Improved scatter chart parsing and empty-data handling.
* Hardened shortcode attribute validation and legacy source rank detection.

= 1.1.0 =
* Added format-specific shortcodes for Standard and Wild meta distributions.
* Supported real-time rank switching on the frontend.
* Supported dynamic class pre-selection and class-locking for Vicious Syndicate radars.
* Added a robust guide page under the Tools admin menu.
* Optimized responsive styling for Blocksy and Newspaper theme layouts.

= 1.0.0 =
* Initial release of the plugin supporting interactive HTML5 Canvas scatter plots and force-directed radars.
