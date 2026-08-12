<?php
/**
 * Plugin Name:       Hearthstone Parses - Interactive Graphs
 * Description:       Integrates high-performance interactive hsguru meta scatter plots (Standard/Wild) and Vicious Syndicate radars into WordPress via simple shortcodes.
 * Version:           1.1.10
 * Requires at least: 6.0
 * Requires PHP:      7.4
 * Author:            Hearthstone Parses Dev Team
 * License:           MIT
 * Text Domain:       hs-parses-graphs-wp
 * Domain Path:       /languages
 *
 * @package HSParsesGraphsWP
 */

defined( 'ABSPATH' ) || exit;

define( 'HS_PARSES_GRAPHS_VERSION', '1.1.10' );
define( 'HS_PARSES_GRAPHS_DIR', plugin_dir_path( __FILE__ ) );
define( 'HS_PARSES_GRAPHS_URL', plugin_dir_url( __FILE__ ) );

/**
 * Register styles and scripts on init but do not enqueue globally.
 * They will be enqueued dynamically in shortcode callbacks.
 */
add_action( 'wp_enqueue_scripts', 'hs_parses_graphs_register_assets' );
function hs_parses_graphs_register_assets() {
	wp_register_style(
		'hs-parses-graphs-style',
		HS_PARSES_GRAPHS_URL . 'assets/css/graphs.css',
		array(),
		HS_PARSES_GRAPHS_VERSION
	);

	wp_register_script(
		'hs-parses-meta-scatter',
		HS_PARSES_GRAPHS_URL . 'assets/js/meta-scatter.js',
		array(),
		HS_PARSES_GRAPHS_VERSION,
		true
	);

	wp_register_script(
		'hs-parses-vs-radar',
		HS_PARSES_GRAPHS_URL . 'assets/js/vs-radar.js',
		array(),
		HS_PARSES_GRAPHS_VERSION,
		true
	);
}

/**
 * Register Shortcodes
 */
add_action( 'init', 'hs_parses_graphs_register_shortcodes' );
function hs_parses_graphs_register_shortcodes() {
	// Backward compatibility
	add_shortcode( 'hs_meta_scatter_chart', 'hs_parses_render_meta_scatter' );
	
	// Modern format-specific scatter charts
	add_shortcode( 'hs_meta_scatter_standard', 'hs_parses_render_meta_scatter_standard' );
	add_shortcode( 'hs_meta_scatter_wild', 'hs_parses_render_meta_scatter_wild' );
	
	// Vicious Syndicate synergy radars
	add_shortcode( 'hs_vs_radar_chart', 'hs_parses_render_vs_radar' );
}

/**
 * Helper to get the default api domain
 */
function hs_parses_graphs_get_default_api_url() {
	return 'https://api.kolodahearthstone.com';
}

function hs_parses_graphs_get_default_archetypes_api_url() {
	return 'https://api.blizzcore.ru';
}

function hs_parses_graphs_sanitize_api_url( $url ) {
	$url = untrailingslashit( esc_url_raw( $url ) );

	return '' === $url ? hs_parses_graphs_get_default_api_url() : $url;
}

function hs_parses_graphs_sanitize_archetypes_api_url( $url ) {
	$url = untrailingslashit( esc_url_raw( $url ) );

	return '' === $url ? hs_parses_graphs_get_default_archetypes_api_url() : $url;
}

function hs_parses_graphs_is_allowed_translation_host( $url ) {
	$host = wp_parse_url( $url, PHP_URL_HOST );

	return in_array( $host, array( 'api.blizzcore.ru', '135.125.171.168' ), true );
}

function hs_parses_graphs_get_archetype_translations_url( $api_url ) {
	$api_url = hs_parses_graphs_sanitize_archetypes_api_url( $api_url );

	if ( ! hs_parses_graphs_is_allowed_translation_host( $api_url ) ) {
		$api_url = hs_parses_graphs_get_default_archetypes_api_url();
	}

	return add_query_arg(
		'base',
		rawurlencode( $api_url ),
		rest_url( 'hs-parses-graphs/v1/archetype-translations' )
	);
}

function hs_parses_graphs_get_card_translations_url() {
	return rest_url( 'hs-parses-graphs/v1/card-translations' );
}

function hs_parses_graphs_sanitize_choice( $value, $allowed, $default ) {
	$value = sanitize_key( $value );

	return in_array( $value, $allowed, true ) ? $value : $default;
}

function hs_parses_graphs_sanitize_dimension( $value, $default, $min = 240, $max = 1600 ) {
	$value = absint( $value );

	if ( $value < $min ) {
		return $default;
	}

	return min( $value, $max );
}

/**
 * Render HSGuru Meta Scatter Plot Shortcode (Generic / Backward Compatibility)
 */
function hs_meta_scatter_chart( $atts ) {
	return hs_parses_render_meta_scatter( $atts );
}

function hs_parses_render_meta_scatter( $atts ) {
	$a = shortcode_atts(
		array(
			'source'        => 'hsguru_meta_standard_diamond_4to1',
			'api_url'       => hs_parses_graphs_get_default_api_url(),
			'archetypes_api_url' => hs_parses_graphs_get_default_archetypes_api_url(),
			'width'         => '850',
			'height'        => '500',
			'show_selector' => 'yes',
		),
		$atts,
		'hs_meta_scatter_chart'
	);

	$source        = sanitize_key( $a['source'] );
	$api_url       = hs_parses_graphs_sanitize_api_url( $a['api_url'] );
	$archetypes_url = hs_parses_graphs_get_archetype_translations_url( $a['archetypes_api_url'] );
	$width         = hs_parses_graphs_sanitize_dimension( $a['width'], 850 );
	$height        = hs_parses_graphs_sanitize_dimension( $a['height'], 500 );
	$show_selector = hs_parses_graphs_sanitize_choice( $a['show_selector'], array( 'yes', 'no' ), 'yes' );

	// Determine format and rank from legacy source name
	$format = 'standard';
	$rank   = 'diamond_4to1';
	if ( strpos( $source, '_wild_' ) !== false ) {
		$format = 'wild';
	}
	if ( strpos( $source, '_top_legend' ) !== false ) {
		$rank = 'top_legend';
	} elseif ( strpos( $source, '_top_5k' ) !== false ) {
		$rank = 'top_5k';
	} elseif ( strpos( $source, '_legend' ) !== false ) {
		$rank = 'legend';
	}

	wp_enqueue_style( 'hs-parses-graphs-style' );
	wp_enqueue_script( 'hs-parses-meta-scatter' );

	ob_start();
	?>
	<div class="hs-meta-scatter-wrapper" 
		data-api-url="<?php echo esc_url( $api_url ); ?>" 
		data-archetypes-url="<?php echo esc_url( $archetypes_url ); ?>"
		data-format="<?php echo esc_attr( $format ); ?>"
		data-start-rank="<?php echo esc_attr( $rank ); ?>"
		data-show-selector="<?php echo esc_attr( $show_selector ); ?>">
		<div class="hs-graph-toolbar">
			<button type="button" class="hs-graph-fullscreen-btn" data-exit-label="<?php echo esc_attr__( 'Закрыть полноэкранный режим', 'hs-parses-graphs-wp' ); ?>">
				<?php echo esc_html__( 'На весь экран', 'hs-parses-graphs-wp' ); ?>
			</button>
		</div>
		
		<p class="muted">
			<?php echo esc_html__( 'Интерактивный график распределения метагейма (Винрейт / Популярность). По вертикали — популярность (%), по горизонтали — процент побед (%). Выберите нужный ранг ниже.', 'hs-parses-graphs-wp' ); ?>
		</p>

		<?php if ( 'yes' === $show_selector ) : ?>
			<div class="hs-meta-scatter-rank-selector" style="margin-bottom: 15px; display: flex; gap: 8px; flex-wrap: wrap;"></div>
		<?php endif; ?>
		
		<div class="hs-meta-scatter-canvas-container" style="background: #1e1e24; border-radius: 8px; padding: 10px; border: 1px solid var(--hs-graph-border);">
			<canvas class="hs-meta-scatter-canvas" 
				width="<?php echo esc_attr( $width ); ?>" 
				height="<?php echo esc_attr( $height ); ?>" 
				style="background: #1e1e24; border-radius: 6px; aspect-ratio: <?php echo esc_attr( $width ); ?>/<?php echo esc_attr( $height ); ?>;"></canvas>
		</div>
		
		<div class="hs-meta-scatter-tooltip">
			<?php echo esc_html__( 'Наведите на точку, чтобы увидеть детали', 'hs-parses-graphs-wp' ); ?>
		</div>
	</div>
	<?php
	return ob_get_clean();
}

/**
 * Render Standard Format Scatter Plot
 * [hs_meta_scatter_standard rank="diamond_4to1" show_selector="yes"]
 */
function hs_parses_render_meta_scatter_standard( $atts ) {
	$a = shortcode_atts(
		array(
			'rank'          => 'diamond_4to1',
			'api_url'       => hs_parses_graphs_get_default_api_url(),
			'archetypes_api_url' => hs_parses_graphs_get_default_archetypes_api_url(),
			'width'         => '850',
			'height'        => '500',
			'show_selector' => 'yes',
		),
		$atts,
		'hs_meta_scatter_standard'
	);

	$rank          = hs_parses_graphs_sanitize_choice( $a['rank'], array( 'top_legend', 'top_5k', 'legend', 'diamond_4to1' ), 'diamond_4to1' );
	$api_url       = hs_parses_graphs_sanitize_api_url( $a['api_url'] );
	$archetypes_url = hs_parses_graphs_get_archetype_translations_url( $a['archetypes_api_url'] );
	$width         = hs_parses_graphs_sanitize_dimension( $a['width'], 850 );
	$height        = hs_parses_graphs_sanitize_dimension( $a['height'], 500 );
	$show_selector = hs_parses_graphs_sanitize_choice( $a['show_selector'], array( 'yes', 'no' ), 'yes' );

	wp_enqueue_style( 'hs-parses-graphs-style' );
	wp_enqueue_script( 'hs-parses-meta-scatter' );

	ob_start();
	?>
	<div class="hs-meta-scatter-wrapper" 
		data-api-url="<?php echo esc_url( $api_url ); ?>" 
		data-archetypes-url="<?php echo esc_url( $archetypes_url ); ?>"
		data-format="standard"
		data-start-rank="<?php echo esc_attr( $rank ); ?>"
		data-show-selector="<?php echo esc_attr( $show_selector ); ?>">
		<div class="hs-graph-toolbar">
			<button type="button" class="hs-graph-fullscreen-btn" data-exit-label="<?php echo esc_attr__( 'Закрыть полноэкранный режим', 'hs-parses-graphs-wp' ); ?>">
				<?php echo esc_html__( 'На весь экран', 'hs-parses-graphs-wp' ); ?>
			</button>
		</div>
		
		<p class="muted">
			<strong><?php echo esc_html__( 'Стандартный формат (Standard):', 'hs-parses-graphs-wp' ); ?></strong>
			<?php echo esc_html__( 'Интерактивный мета-график. Выберите ранг для отображения распределения сил архетипов.', 'hs-parses-graphs-wp' ); ?>
		</p>

		<?php if ( 'yes' === $show_selector ) : ?>
			<div class="hs-meta-scatter-rank-selector" style="margin-bottom: 15px; display: flex; gap: 8px; flex-wrap: wrap;"></div>
		<?php endif; ?>
		
		<div class="hs-meta-scatter-canvas-container" style="background: #1e1e24; border-radius: 8px; padding: 10px; border: 1px solid var(--hs-graph-border);">
			<canvas class="hs-meta-scatter-canvas" 
				width="<?php echo esc_attr( $width ); ?>" 
				height="<?php echo esc_attr( $height ); ?>" 
				style="background: #1e1e24; border-radius: 6px; aspect-ratio: <?php echo esc_attr( $width ); ?>/<?php echo esc_attr( $height ); ?>;"></canvas>
		</div>
		
		<div class="hs-meta-scatter-tooltip">
			<?php echo esc_html__( 'Наведите на точку, чтобы увидеть детали', 'hs-parses-graphs-wp' ); ?>
		</div>
	</div>
	<?php
	return ob_get_clean();
}

/**
 * Render Wild Format Scatter Plot
 * [hs_meta_scatter_wild rank="legend" show_selector="yes"]
 */
function hs_parses_render_meta_scatter_wild( $atts ) {
	$a = shortcode_atts(
		array(
			'rank'          => 'legend',
			'api_url'       => hs_parses_graphs_get_default_api_url(),
			'archetypes_api_url' => hs_parses_graphs_get_default_archetypes_api_url(),
			'width'         => '850',
			'height'        => '500',
			'show_selector' => 'yes',
		),
		$atts,
		'hs_meta_scatter_wild'
	);

	$rank          = hs_parses_graphs_sanitize_choice( $a['rank'], array( 'top_legend', 'top_5k', 'legend', 'diamond_4to1' ), 'legend' );
	$api_url       = hs_parses_graphs_sanitize_api_url( $a['api_url'] );
	$archetypes_url = hs_parses_graphs_get_archetype_translations_url( $a['archetypes_api_url'] );
	$width         = hs_parses_graphs_sanitize_dimension( $a['width'], 850 );
	$height        = hs_parses_graphs_sanitize_dimension( $a['height'], 500 );
	$show_selector = hs_parses_graphs_sanitize_choice( $a['show_selector'], array( 'yes', 'no' ), 'yes' );

	wp_enqueue_style( 'hs-parses-graphs-style' );
	wp_enqueue_script( 'hs-parses-meta-scatter' );

	ob_start();
	?>
	<div class="hs-meta-scatter-wrapper" 
		data-api-url="<?php echo esc_url( $api_url ); ?>" 
		data-archetypes-url="<?php echo esc_url( $archetypes_url ); ?>"
		data-format="wild"
		data-start-rank="<?php echo esc_attr( $rank ); ?>"
		data-show-selector="<?php echo esc_attr( $show_selector ); ?>">
		<div class="hs-graph-toolbar">
			<button type="button" class="hs-graph-fullscreen-btn" data-exit-label="<?php echo esc_attr__( 'Закрыть полноэкранный режим', 'hs-parses-graphs-wp' ); ?>">
				<?php echo esc_html__( 'На весь экран', 'hs-parses-graphs-wp' ); ?>
			</button>
		</div>
		
		<p class="muted">
			<strong><?php echo esc_html__( 'Вольный формат (Wild):', 'hs-parses-graphs-wp' ); ?></strong>
			<?php echo esc_html__( 'Интерактивный мета-график. Выберите ранг для отображения распределения сил архетипов в Вольном режиме.', 'hs-parses-graphs-wp' ); ?>
		</p>

		<?php if ( 'yes' === $show_selector ) : ?>
			<div class="hs-meta-scatter-rank-selector" style="margin-bottom: 15px; display: flex; gap: 8px; flex-wrap: wrap;"></div>
		<?php endif; ?>
		
		<div class="hs-meta-scatter-canvas-container" style="background: #1e1e24; border-radius: 8px; padding: 10px; border: 1px solid var(--hs-graph-border);">
			<canvas class="hs-meta-scatter-canvas" 
				width="<?php echo esc_attr( $width ); ?>" 
				height="<?php echo esc_attr( $height ); ?>" 
				style="background: #1e1e24; border-radius: 6px; aspect-ratio: <?php echo esc_attr( $width ); ?>/<?php echo esc_attr( $height ); ?>;"></canvas>
		</div>
		
		<div class="hs-meta-scatter-tooltip">
			<?php echo esc_html__( 'Наведите на точку, чтобы увидеть детали', 'hs-parses-graphs-wp' ); ?>
		</div>
	</div>
	<?php
	return ob_get_clean();
}

/**
 * Render Vicious Syndicate Radar Shortcode with Class Select & Locks
 * [hs_vs_radar_chart class="Druid" lock_class="no" width="750" height="750"]
 */
function hs_parses_render_vs_radar( $atts ) {
	$a = shortcode_atts(
		array(
			'api_url'    => hs_parses_graphs_get_default_api_url(),
			'archetypes_api_url' => hs_parses_graphs_get_default_archetypes_api_url(),
			'class'      => '',
			'lock_class' => 'no',
			'width'      => '750',
			'height'     => '750',
		),
		$atts,
		'hs_vs_radar_chart'
	);

	$api_url    = hs_parses_graphs_sanitize_api_url( $a['api_url'] );
	$archetypes_url = hs_parses_graphs_get_archetype_translations_url( $a['archetypes_api_url'] );
	$card_translations_url = hs_parses_graphs_get_card_translations_url();
	$class      = sanitize_key( $a['class'] );
	$lock_class = hs_parses_graphs_sanitize_choice( $a['lock_class'], array( 'yes', 'no' ), 'no' );
	$width      = hs_parses_graphs_sanitize_dimension( $a['width'], 750 );
	$height     = hs_parses_graphs_sanitize_dimension( $a['height'], 750 );

	wp_enqueue_style( 'hs-parses-graphs-style' );
	wp_enqueue_script( 'hs-parses-vs-radar' );

	ob_start();
	?>
	<div class="hs-vs-radar-wrapper" 
		data-api-url="<?php echo esc_url( $api_url ); ?>"
		data-archetypes-url="<?php echo esc_url( $archetypes_url ); ?>"
		data-card-translations-url="<?php echo esc_url( $card_translations_url ); ?>"
		data-start-class="<?php echo esc_attr( $class ); ?>"
		data-lock-class="<?php echo esc_attr( $lock_class ); ?>">
		<div class="hs-graph-toolbar">
			<button type="button" class="hs-graph-fullscreen-btn" data-exit-label="<?php echo esc_attr__( 'Закрыть полноэкранный режим', 'hs-parses-graphs-wp' ); ?>">
				<?php echo esc_html__( 'На весь экран', 'hs-parses-graphs-wp' ); ?>
			</button>
		</div>
		
		<p class="muted">
			<?php echo esc_html__( 'Vicious Syndicate: связи карт внутри выбранного класса и архетипа.', 'hs-parses-graphs-wp' ); ?>
		</p>

		<!-- Class Tab Buttons (Automatically hidden in JS if lock_class="yes") -->
		<div class="hs-vs-class-tabs"></div>
		
		<!-- Archetype Tab Buttons -->
		<div class="hs-vs-archetype-tabs"></div>

		<!-- Deck Code section -->
		<div class="hs-vs-deck-code-section" style="margin: 15px 0; display: none; background: rgba(255,255,255,0.03); padding: 12px; border: 1px dashed var(--hs-graph-border); border-radius: 8px;">
			<span style="font-size: 0.9rem; margin-right: 10px; color: var(--hs-graph-accent); font-weight: bold;">
				<?php echo esc_html__( 'Код колоды:', 'hs-parses-graphs-wp' ); ?>
			</span>
			<input type="text" class="hs-vs-deck-code-input" readonly style="width: 250px; max-width: calc(100% - 130px); background: #1a1a24; border: 1px solid var(--hs-graph-border); color: #fff; padding: 6px 10px; border-radius: 6px; font-size: 0.85rem;" />
			<button class="hs-vs-deck-code-copy-btn" style="background: #2ec4b6; border: none; color: #fff; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: bold; margin-left: 5px;">
				<?php echo esc_html__( 'Копировать', 'hs-parses-graphs-wp' ); ?>
			</button>
		</div>

		<div class="hs-vs-radar-layout">
			<!-- Canvas Graph -->
			<div class="hs-vs-graph-column">
				<div class="hs-vs-canvas-shell">
					<canvas class="hs-vs-radar-canvas" 
						width="<?php echo esc_attr( $width ); ?>" 
						height="<?php echo esc_attr( $height ); ?>" 
						style="aspect-ratio: <?php echo esc_attr( $width ); ?>/<?php echo esc_attr( $height ); ?>;"></canvas>
				</div>
				<div class="hs-vs-radar-hover-info">
					<?php echo esc_html__( 'Наведите на карту для деталей', 'hs-parses-graphs-wp' ); ?>
				</div>
			</div>

			<!-- Node Info Panel -->
			<div class="hs-vs-side-panel">
				<!-- Search -->
				<div>
					<input type="text" class="hs-vs-radar-search" placeholder="<?php echo esc_attr__( 'Поиск карты...', 'hs-parses-graphs-wp' ); ?>" />
				</div>

				<!-- Selected Card Details -->
				<div class="hs-vs-selected-card-panel">
					<h4 class="hs-vs-selected-card-title">
						<?php echo esc_html__( 'Выберите карту на графе', 'hs-parses-graphs-wp' ); ?>
					</h4>
					<div class="hs-vs-selected-card-details">
						<?php echo esc_html__( 'Нажмите на любой узел на графе, чтобы увидеть его сильные связи с другими картами.', 'hs-parses-graphs-wp' ); ?>
					</div>
				</div>

				<!-- Nodes Table List -->
				<div class="hs-vs-table-scroll">
					<table class="hs-vs-nodes-table">
						<thead>
							<tr>
								<th><?php echo esc_html__( 'Карта', 'hs-parses-graphs-wp' ); ?></th>
								<th><?php echo esc_html__( 'Поп.', 'hs-parses-graphs-wp' ); ?></th>
								<th><?php echo esc_html__( 'Связей', 'hs-parses-graphs-wp' ); ?></th>
							</tr>
						</thead>
						<tbody></tbody>
					</table>
				</div>

				<button class="hs-vs-radar-reset-btn">
					<?php echo esc_html__( 'Сбросить фильтры', 'hs-parses-graphs-wp' ); ?>
				</button>
			</div>
		</div>
	</div>
	<?php
	return ob_get_clean();
}

/**
 * Public REST endpoints for cached translation data used by frontend graphs.
 */
add_action( 'rest_api_init', 'hs_parses_graphs_register_rest_routes' );
function hs_parses_graphs_register_rest_routes() {
	register_rest_route(
		'hs-parses-graphs/v1',
		'/archetype-translations',
		array(
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'hs_parses_graphs_rest_archetype_translations',
			'permission_callback' => '__return_true',
			'args'                => array(
				'base' => array(
					'type'              => 'string',
					'required'          => false,
					'sanitize_callback' => 'sanitize_text_field',
				),
			),
		)
	);

	register_rest_route(
		'hs-parses-graphs/v1',
		'/card-translations',
		array(
			'methods'             => array( WP_REST_Server::READABLE, WP_REST_Server::CREATABLE ),
			'callback'            => 'hs_parses_graphs_rest_card_translations',
			'permission_callback' => '__return_true',
		)
	);
}

function hs_parses_graphs_fetch_json( $url, $timeout = 20 ) {
	$response = wp_remote_get(
		$url,
		array(
			'timeout'     => $timeout,
			'redirection' => 3,
			'user-agent'  => 'hs-parses-graphs-wp/' . HS_PARSES_GRAPHS_VERSION . '; ' . home_url( '/' ),
		)
	);

	if ( is_wp_error( $response ) ) {
		return $response;
	}

	$status = wp_remote_retrieve_response_code( $response );
	if ( $status < 200 || $status >= 300 ) {
		return new WP_Error( 'hs_parses_graphs_remote_error', 'Remote API returned an error.', array( 'status' => $status ) );
	}

	$body = wp_remote_retrieve_body( $response );
	if ( '' === $body ) {
		return new WP_Error( 'hs_parses_graphs_empty_response', 'Remote API returned an empty response.' );
	}

	$data = json_decode( $body, true );
	if ( ! is_array( $data ) ) {
		return new WP_Error( 'hs_parses_graphs_bad_json', 'Remote API returned invalid JSON.' );
	}

	return $data;
}

function hs_parses_graphs_rest_archetype_translations( WP_REST_Request $request ) {
	$base = rawurldecode( (string) $request->get_param( 'base' ) );
	$base = hs_parses_graphs_sanitize_archetypes_api_url( $base );

	if ( ! hs_parses_graphs_is_allowed_translation_host( $base ) ) {
		$base = hs_parses_graphs_get_default_archetypes_api_url();
	}

	$cache_key = 'hs_parses_arch_' . md5( $base );
	$cached    = get_transient( $cache_key );
	if ( is_array( $cached ) ) {
		return rest_ensure_response( $cached );
	}

	$endpoints = array(
		$base . '/archetypes?limit=500',
		$base . '/public/archetypes',
	);
	$map       = array();
	$source    = '';
	$error     = null;

	foreach ( $endpoints as $endpoint ) {
		$data = hs_parses_graphs_fetch_json( $endpoint, 20 );
		if ( is_wp_error( $data ) ) {
			$error = $data;
			continue;
		}

		$items = array();
		if ( isset( $data['items'] ) && is_array( $data['items'] ) ) {
			$items = $data['items'];
		} elseif ( isset( $data[0] ) ) {
			$items = $data;
		}

		foreach ( $items as $item ) {
			if ( ! is_array( $item ) ) {
				continue;
			}

			$en = isset( $item['name_en'] ) ? $item['name_en'] : ( $item['eng'] ?? '' );
			$ru = isset( $item['name_ru'] ) ? $item['name_ru'] : ( $item['rus'] ?? '' );
			$en = trim( wp_strip_all_tags( (string) $en ) );
			$ru = trim( wp_strip_all_tags( (string) $ru ) );

			if ( '' !== $en && '' !== $ru ) {
				$map[ $en ] = $ru;
			}
		}

		if ( $map ) {
			$source = $endpoint;
			break;
		}
	}

	if ( ! $map ) {
		return is_wp_error( $error ) ? $error : new WP_Error( 'hs_parses_graphs_no_archetypes', 'No archetype translations found.' );
	}

	$payload = array(
		'success' => true,
		'source'  => $source,
		'count'   => count( $map ),
		'items'   => $map,
	);

	set_transient( $cache_key, $payload, 6 * HOUR_IN_SECONDS );

	return rest_ensure_response( $payload );
}

function hs_parses_graphs_get_card_render_url( $card_id ) {
	$card_id = trim( (string) $card_id );
	if ( '' === $card_id ) {
		return '';
	}

	return 'https://art.hearthstonejson.com/v1/render/latest/ruRU/256x/' . rawurlencode( $card_id ) . '.png';
}

function hs_parses_graphs_get_tooltip_image_url( $image_raw ) {
	$image_raw = esc_url_raw( $image_raw );
	if ( '' === $image_raw ) {
		return '';
	}

	if ( function_exists( 'hs_smart_tooltip_image_proxy_url' ) ) {
		return hs_smart_tooltip_image_proxy_url( $image_raw );
	}

	return $image_raw;
}

function hs_parses_graphs_normalize_card_rarity( $rarity ) {
	$rarity = strtolower( sanitize_key( (string) $rarity ) );

	return in_array( $rarity, array( 'common', 'rare', 'epic', 'legendary' ), true ) ? $rarity : 'common';
}

function hs_parses_graphs_normalize_card_match_key( $name ) {
	$name = strtolower( remove_accents( (string) $name ) );
	$name = preg_replace( '/[^a-z0-9]+/', ' ', $name );
	$name = preg_replace( '/\s+/', ' ', $name );

	return trim( (string) $name );
}

function hs_parses_graphs_card_candidate_score( $en_name, $ru_name, $card ) {
	$type  = isset( $card['type'] ) ? strtoupper( (string) $card['type'] ) : '';
	$score = 0;

	if ( ! empty( $card['collectible'] ) ) {
		$score += 100;
	}
	if ( '' !== $ru_name && 0 !== strcasecmp( (string) $en_name, (string) $ru_name ) ) {
		$score += 70;
	}
	if ( in_array( $type, array( 'MINION', 'SPELL', 'WEAPON', 'LOCATION', 'HERO' ), true ) ) {
		$score += 30;
	}
	if ( ! empty( $card['rarity'] ) ) {
		$score += 15;
	}
	if ( 'ENCHANTMENT' === $type || 'HERO_POWER' === $type ) {
		$score -= 80;
	}

	return $score;
}

function hs_parses_graphs_rest_card_translations( WP_REST_Request $request ) {
	$cache_key = 'hs_parses_ru_card_map_v3';
	$cached    = get_transient( $cache_key );
	if ( is_array( $cached ) ) {
		return rest_ensure_response( hs_parses_graphs_filter_card_translation_payload( $cached, $request ) );
	}

	$en_cards = hs_parses_graphs_fetch_json( 'https://api.hearthstonejson.com/v1/latest/enUS/cards.json', 45 );
	if ( is_wp_error( $en_cards ) ) {
		return $en_cards;
	}

	$ru_cards = hs_parses_graphs_fetch_json( 'https://api.hearthstonejson.com/v1/latest/ruRU/cards.json', 45 );
	if ( is_wp_error( $ru_cards ) ) {
		return $ru_cards;
	}

	$ru_by_key = array();
	foreach ( $ru_cards as $card ) {
		if ( ! is_array( $card ) || empty( $card['name'] ) ) {
			continue;
		}

		if ( isset( $card['dbfId'] ) ) {
			$ru_by_key[ 'dbf:' . (string) $card['dbfId'] ] = (string) $card['name'];
		}
		if ( ! empty( $card['id'] ) ) {
			$ru_by_key[ 'id:' . (string) $card['id'] ] = (string) $card['name'];
		}
	}

	$map    = array();
	$meta   = array();
	$scores = array();
	foreach ( $en_cards as $card ) {
		if ( ! is_array( $card ) || empty( $card['name'] ) ) {
			continue;
		}

		$en_name = (string) $card['name'];
		$ru_name = '';
		if ( isset( $card['dbfId'] ) && isset( $ru_by_key[ 'dbf:' . (string) $card['dbfId'] ] ) ) {
			$ru_name = $ru_by_key[ 'dbf:' . (string) $card['dbfId'] ];
		} elseif ( ! empty( $card['id'] ) && isset( $ru_by_key[ 'id:' . (string) $card['id'] ] ) ) {
			$ru_name = $ru_by_key[ 'id:' . (string) $card['id'] ];
		}

		if ( '' !== $ru_name ) {
			$card_id   = empty( $card['id'] ) ? '' : (string) $card['id'];
			$image_raw = hs_parses_graphs_get_card_render_url( $card_id );
			$score     = hs_parses_graphs_card_candidate_score( $en_name, $ru_name, $card );

			if ( isset( $scores[ $en_name ] ) && $scores[ $en_name ] > $score ) {
				continue;
			}

			$map[ $en_name ]  = $ru_name;
			$meta[ $en_name ] = array(
				'name_ru'   => $ru_name,
				'id'        => $card_id,
				'dbfId'     => isset( $card['dbfId'] ) ? (string) $card['dbfId'] : '',
				'rarity'    => hs_parses_graphs_normalize_card_rarity( $card['rarity'] ?? '' ),
				'image'     => hs_parses_graphs_get_tooltip_image_url( $image_raw ),
				'image_raw' => $image_raw,
			);
			$scores[ $en_name ] = $score;
		}
	}

	if ( ! $map ) {
		return new WP_Error( 'hs_parses_graphs_no_cards', 'No card translations found.' );
	}

	$payload = array(
		'success' => true,
		'locale'  => 'ruRU',
		'source'  => 'https://api.hearthstonejson.com/v1/latest',
		'count'   => count( $map ),
		'items'   => $map,
		'meta'    => $meta,
		'keys'    => array_combine(
			array_map( 'hs_parses_graphs_normalize_card_match_key', array_keys( $map ) ),
			array_keys( $map )
		),
	);

	set_transient( $cache_key, $payload, 7 * DAY_IN_SECONDS );

	return rest_ensure_response( hs_parses_graphs_filter_card_translation_payload( $payload, $request ) );
}

function hs_parses_graphs_filter_card_translation_payload( $payload, WP_REST_Request $request ) {
	$names_value = $request->get_param( 'names' );
	if ( empty( $names_value ) || empty( $payload['items'] ) || ! is_array( $payload['items'] ) ) {
		$result = $payload;
		unset( $result['meta'], $result['keys'] );
		return $result;
	}

	if ( is_array( $names_value ) ) {
		$decoded = $names_value;
	} else {
		$names_raw = (string) $names_value;
		$decoded   = json_decode( rawurldecode( $names_raw ), true );
		if ( ! is_array( $decoded ) ) {
			$decoded = explode( ',', rawurldecode( $names_raw ) );
		}
	}

	$wanted = array();
	foreach ( $decoded as $name ) {
		$name = trim( wp_strip_all_tags( (string) $name ) );
		if ( '' !== $name ) {
			$wanted[ $name ] = true;
		}
	}

	if ( ! $wanted ) {
		return $payload;
	}

	$filtered      = array();
	$filtered_meta = array();
	foreach ( array_keys( $wanted ) as $name ) {
		$source_name = hs_parses_graphs_find_card_translation_source_name( $name, $payload );
		if ( '' === $source_name ) {
			continue;
		}

		if ( isset( $payload['items'][ $source_name ] ) ) {
			$filtered[ $name ] = $payload['items'][ $source_name ];
		}
		if ( ! empty( $payload['meta'][ $source_name ] ) && is_array( $payload['meta'][ $source_name ] ) ) {
			$filtered_meta[ $name ]                = $payload['meta'][ $source_name ];
			$filtered_meta[ $name ]['source_name'] = $source_name;
		}
	}

	$result          = $payload;
	$result['count'] = count( $filtered );
	$result['items'] = $filtered;
	$result['meta']  = $filtered_meta;
	$result['scope'] = 'requested_names';
	unset( $result['keys'] );

	return $result;
}

function hs_parses_graphs_find_card_translation_source_name( $requested_name, $payload ) {
	if ( isset( $payload['items'][ $requested_name ] ) ) {
		return $requested_name;
	}

	if ( empty( $payload['keys'] ) || ! is_array( $payload['keys'] ) ) {
		return '';
	}

	$requested_key = hs_parses_graphs_normalize_card_match_key( $requested_name );
	if ( '' === $requested_key ) {
		return '';
	}

	if ( isset( $payload['keys'][ $requested_key ] ) ) {
		return $payload['keys'][ $requested_key ];
	}

	if ( strlen( $requested_key ) < 8 ) {
		return '';
	}

	$best_name = '';
	$best_gap  = PHP_INT_MAX;
	foreach ( $payload['keys'] as $candidate_key => $candidate_name ) {
		if ( 0 === strpos( $candidate_key, $requested_key ) ) {
			$gap = strlen( $candidate_key ) - strlen( $requested_key );
			if ( $gap < $best_gap ) {
				$best_gap  = $gap;
				$best_name = $candidate_name;
			}
		}
	}

	return $best_name;
}

/**
 * Register Admin Menu under Tools
 */
add_action( 'admin_menu', 'hs_parses_graphs_register_tools_page' );
function hs_parses_graphs_register_tools_page() {
	add_management_page(
		__( 'Графики Hearthstone', 'hs-parses-graphs-wp' ),
		__( 'Графики Hearthstone', 'hs-parses-graphs-wp' ),
		'manage_options',
		'hs-parses-graphs-documentation',
		'hs_parses_graphs_render_tools_page'
	);
}

/**
 * Render the Tools Admin Page Layout
 */
function hs_parses_graphs_render_tools_page() {
	if ( ! current_user_can( 'manage_options' ) ) {
		wp_die( esc_html__( 'Извините, у вас нет достаточных прав для доступа к этой странице.', 'hs-parses-graphs-wp' ) );
	}
	?>
	<div class="wrap" style="max-width: 1100px; margin-top: 20px; font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen-Sans,Ubuntu,Cantarell,sans-serif;">
		<h1 style="font-weight: 700; margin-bottom: 5px; color: #23282d;">
			<?php echo esc_html__( 'Интерактивные графики Hearthstone', 'hs-parses-graphs-wp' ); ?>
		</h1>
		<p class="description" style="font-size: 1rem; margin-bottom: 25px; color: #555d66;">
			<?php echo esc_html__( 'Руководство и список доступных шорткодов для размещения интерактивных визуализаций HSGuru и Vicious Syndicate.', 'hs-parses-graphs-wp' ); ?>
		</p>

		<!-- Card Layout -->
		<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 30px;">
			
			<!-- Card 1 -->
			<div style="background: #fff; border: 1px solid #ccd0d4; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.04);">
				<span style="display: inline-block; padding: 4px 8px; background: #2ec4b6; color: #fff; font-size: 0.75rem; font-weight: bold; border-radius: 4px; margin-bottom: 12px; text-transform: uppercase;">
					Standard Meta
				</span>
				<h3 style="margin: 0 0 10px 0; font-size: 1.2rem; color: #1e1e1e;">[hs_meta_scatter_standard]</h3>
				<p style="color: #646970; font-size: 0.9rem; line-height: 1.5; min-height: 60px;">
					<?php echo esc_html__( 'Отображает интерактивное распределение винрейта и популярности архетипов Стандартного формата на холсте HTML5 Canvas.', 'hs-parses-graphs-wp' ); ?>
				</p>
				<div style="background: #f6f7f7; padding: 10px; border-radius: 6px; border-left: 4px solid #2ec4b6; margin-bottom: 15px;">
					<code id="sc-code-std" style="font-family: Consolas, Monaco, monospace; font-size: 0.85rem; color: #000;">[hs_meta_scatter_standard rank="diamond_4to1" show_selector="yes"]</code>
				</div>
				<button class="button button-secondary" onclick="copyTextToClipboard('sc-code-std', this)">
					<?php echo esc_html__( 'Скопировать шорткод', 'hs-parses-graphs-wp' ); ?>
				</button>
			</div>

			<!-- Card 2 -->
			<div style="background: #fff; border: 1px solid #ccd0d4; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.04);">
				<span style="display: inline-block; padding: 4px 8px; background: #ff9f1c; color: #000; font-size: 0.75rem; font-weight: bold; border-radius: 4px; margin-bottom: 12px; text-transform: uppercase;">
					Wild Meta
				</span>
				<h3 style="margin: 0 0 10px 0; font-size: 1.2rem; color: #1e1e1e;">[hs_meta_scatter_wild]</h3>
				<p style="color: #646970; font-size: 0.9rem; line-height: 1.5; min-height: 60px;">
					<?php echo esc_html__( 'Отображает интерактивное распределение винрейта и популярности архетипов Вольного формата на холсте HTML5 Canvas.', 'hs-parses-graphs-wp' ); ?>
				</p>
				<div style="background: #f6f7f7; padding: 10px; border-radius: 6px; border-left: 4px solid #ff9f1c; margin-bottom: 15px;">
					<code id="sc-code-wild" style="font-family: Consolas, Monaco, monospace; font-size: 0.85rem; color: #000;">[hs_meta_scatter_wild rank="legend" show_selector="yes"]</code>
				</div>
				<button class="button button-secondary" onclick="copyTextToClipboard('sc-code-wild', this)">
					<?php echo esc_html__( 'Скопировать шорткод', 'hs-parses-graphs-wp' ); ?>
				</button>
			</div>

			<!-- Card 3 -->
			<div style="background: #fff; border: 1px solid #ccd0d4; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.04);">
				<span style="display: inline-block; padding: 4px 8px; background: #e71d36; color: #fff; font-size: 0.75rem; font-weight: bold; border-radius: 4px; margin-bottom: 12px; text-transform: uppercase;">
					Synergy Radar
				</span>
				<h3 style="margin: 0 0 10px 0; font-size: 1.2rem; color: #1e1e1e;">[hs_vs_radar_chart]</h3>
				<p style="color: #646970; font-size: 0.9rem; line-height: 1.5; min-height: 60px;">
					<?php echo esc_html__( 'Отображает интерактивный физический граф синергий карт Vicious Syndicate с выбором класса и архетипов.', 'hs-parses-graphs-wp' ); ?>
				</p>
				<div style="background: #f6f7f7; padding: 10px; border-radius: 6px; border-left: 4px solid #e71d36; margin-bottom: 15px;">
					<code id="sc-code-radar" style="font-family: Consolas, Monaco, monospace; font-size: 0.85rem; color: #000;">[hs_vs_radar_chart class="Druid" lock_class="yes"]</code>
				</div>
				<button class="button button-secondary" onclick="copyTextToClipboard('sc-code-radar', this)">
					<?php echo esc_html__( 'Скопировать шорткод', 'hs-parses-graphs-wp' ); ?>
				</button>
			</div>

		</div>

		<!-- Advanced attributes reference -->
		<div style="background: #fff; border: 1px solid #ccd0d4; border-radius: 8px; padding: 25px; box-shadow: 0 1px 3px rgba(0,0,0,.04); margin-bottom: 30px;">
			<h2 style="font-size: 1.3rem; font-weight: bold; margin-top: 0; margin-bottom: 15px; color: #1e1e1e;">
				<?php echo esc_html__( 'Подробная спецификация параметров шорткодов', 'hs-parses-graphs-wp' ); ?>
			</h2>

			<table class="wp-list-table widefat fixed striped pages" style="border: 1px solid #e5e5e5; box-shadow: none; border-radius: 4px; overflow: hidden; margin-bottom: 20px;">
				<thead>
					<tr>
						<th style="font-weight: bold; padding: 10px 15px;"><?php echo esc_html__( 'Параметр', 'hs-parses-graphs-wp' ); ?></th>
						<th style="font-weight: bold; padding: 10px 15px;"><?php echo esc_html__( 'Допустимые значения', 'hs-parses-graphs-wp' ); ?></th>
						<th style="font-weight: bold; padding: 10px 15px;"><?php echo esc_html__( 'Описание', 'hs-parses-graphs-wp' ); ?></th>
					</tr>
				</thead>
				<tbody>
					<!-- Rank -->
					<tr>
						<td style="padding: 12px 15px;"><strong><code>rank</code></strong></td>
						<td style="padding: 12px 15px;"><code>top_legend</code>, <code>top_5k</code>, <code>legend</code>, <code>diamond_4to1</code></td>
						<td style="padding: 12px 15px;">
							<?php echo esc_html__( 'Определяет стартовый ранг данных при загрузке страницы. По умолчанию используется diamond_4to1.', 'hs-parses-graphs-wp' ); ?>
						</td>
					</tr>
					<!-- Show Selector -->
					<tr>
						<td style="padding: 12px 15px;"><strong><code>show_selector</code></strong></td>
						<td style="padding: 12px 15px;"><code>yes</code>, <code>no</code></td>
						<td style="padding: 12px 15px;">
							<?php echo esc_html__( 'Показывает или скрывает кнопки быстрого переключения рангов непосредственно на странице (по умолчанию yes).', 'hs-parses-graphs-wp' ); ?>
						</td>
					</tr>
					<!-- Class -->
					<tr>
						<td style="padding: 12px 15px;"><strong><code>class</code></strong></td>
						<td style="padding: 12px 15px;"><code>DeathKnight</code>, <code>DemonHunter</code>, <code>Druid</code>, <code>Hunter</code>, <code>Mage</code>, <code>Paladin</code>, <code>Priest</code>, <code>Rogue</code>, <code>Shaman</code>, <code>Warlock</code>, <code>Warrior</code></td>
						<td style="padding: 12px 15px;">
							<?php echo esc_html__( 'Стартовый класс для предвыбора во Vicious Syndicate радаре. Позволяет мгновенно открывать конкретную класс-карту при загрузке.', 'hs-parses-graphs-wp' ); ?>
						</td>
					</tr>
					<!-- Lock Class -->
					<tr>
						<td style="padding: 12px 15px;"><strong><code>lock_class</code></strong></td>
						<td style="padding: 12px 15px;"><code>yes</code>, <code>no</code></td>
						<td style="padding: 12px 15px;">
							<?php echo esc_html__( 'Если установлено в "yes", скрывает глобальные вкладки выбора классов, фиксируя радар на одном выбранном классе (с возможностью переключения его архетипов). Отлично подходит для класс-специфичных постов.', 'hs-parses-graphs-wp' ); ?>
						</td>
					</tr>
					<!-- Width/Height -->
					<tr>
						<td style="padding: 12px 15px;"><strong><code>width</code> / <code>height</code></strong></td>
						<td style="padding: 12px 15px;">Числа в пикселях (например, <code>850</code>, <code>500</code>)</td>
						<td style="padding: 12px 15px;">
							<?php echo esc_html__( 'Управляет размерами и соотношением сторон холста интерактивных Canvas графиков.', 'hs-parses-graphs-wp' ); ?>
						</td>
					</tr>
				</tbody>
			</table>

			<div style="background: #f0f6fc; border-left: 4px solid #72aee6; padding: 15px; border-radius: 4px;">
				<h4 style="margin-top: 0; margin-bottom: 5px; color: #1d2327; font-weight: bold;"><?php echo esc_html__( 'Премиальная совместимость с темами Blocksy и Newspaper', 'hs-parses-graphs-wp' ); ?></h4>
				<p style="margin: 0; color: #2c3338; font-size: 0.9rem; line-height: 1.4;">
					<?php echo esc_html__( 'Стили плагина полностью изолированы и используют гибкую адаптивную верстку CSS Grid, Flexbox и CSS Variables. Настройка корректно наследует шрифты темы, прекрасно работает как на светлых, так и на темных макетах, а также поддерживает адаптивный рендеринг на мобильных дисплеях.', 'hs-parses-graphs-wp' ); ?>
				</p>
			</div>
		</div>
	</div>

	<!-- Copy scripts -->
	<script>
		function copyTextToClipboard(elementId, btn) {
			var text = document.getElementById(elementId).textContent;
			navigator.clipboard.writeText(text).then(function() {
				var oldLabel = btn.textContent;
				btn.textContent = '<?php echo esc_js( __( 'Успешно скопировано!', 'hs-parses-graphs-wp' ) ); ?>';
				btn.style.background = '#46b450';
				btn.style.color = '#fff';
				btn.style.borderColor = '#46b450';
				setTimeout(function() {
					btn.textContent = oldLabel;
					btn.style.background = '';
					btn.style.color = '';
					btn.style.borderColor = '';
				}, 1500);
			});
		}
	</script>
	<?php
}
