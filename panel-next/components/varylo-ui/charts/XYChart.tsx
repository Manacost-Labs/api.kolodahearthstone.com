'use client';

import { useMemo, useState, type ComponentPropsWithRef, type PointerEvent } from 'react';
import { clamp, cx } from '../primitives';
import { Skeleton } from '../basic/Skeleton';
import {
  AXIS_GAP,
  CategoryLabels,
  ChartAnnouncer,
  ChartEmpty,
  GlowFilter,
  ValueGrid,
  axisTickFormat,
  crisp,
  describeRow,
  useActiveIndex,
  valueAxis,
} from './cartesian';
import { ChartDataTable } from './ChartDataTable';
import { ChartLegend } from './ChartLegend';
import { ChartTooltip } from './ChartTooltip';
import { areaPath, linePath, scaleLinear, splitDefined, type Curve, type Point } from './scale';
import { useChartSize } from './useChartSize';
import {
  cssVars,
  formatAxisValue,
  formatTitleValue,
  seriesColor,
  toNumber,
  truncateText,
  useSvgId,
  useTextMeasure,
  valueFormat,
  type ChartSeries,
  type ValueFormatter,
} from './utils';
import { useLocale } from '../theme/ThemeProvider';
import { useThemeTokens } from '../theme/useThemeTokens';
import './Chart.css';

export interface XYChartLabels {
  /** Shown instead of the plot when there is nothing to draw. Default "No data". */
  empty: string;
  /** Tooltip row with the sum of a stacked chart. Default "Total". */
  total: string;
}

const DEFAULT_LABELS: XYChartLabels = { empty: 'No data', total: 'Total' };

export interface XYChartProps<T extends object, X extends keyof T & string = keyof T & string>
  extends Omit<ComponentPropsWithRef<'div'>, 'children'> {
  /** Rows to plot, one per x position; a missing or non-numeric value breaks that series' line. */
  data: T[];
  /** Field used for the x axis (dates or categories), plotted in data order. */
  x: X;
  /** Numeric fields drawn as lines (or areas), each with its legend label and optional color. */
  series: ChartSeries<T>[];
  /** Plot height in px; width follows the container. Default 240. */
  height?: number;
  /** Default: the theme's `--ui-chart-curve` (`linear` in Vercel, `monotone` in Glass). */
  curve?: Curve;
  /** Formats values in the tooltip and data table. Axis ticks keep its units around a shorter number ("$15K"). */
  valueFormatter?: ValueFormatter;
  /** Formats the value-axis ticks instead of the shortened `valueFormatter`. */
  tickFormatter?: ValueFormatter;
  /** Formats x values for axis labels and the tooltip title. */
  xFormatter?: (value: T[X], index: number) => string;
  /** BCP 47 locale for the default number and date formatting. Default: the `ThemeProvider` locale, then the browser's. */
  locale?: string;
  /** Header of the x column in the accessible data table. Default: the `x` key. */
  xLabel?: string;
  /** Overrides for the built-in strings. */
  labels?: Partial<XYChartLabels>;
  /** Horizontal grid lines at the value ticks; the zero line is drawn either way. */
  showGrid?: boolean;
  /** Default: shown when there is more than one series. */
  showLegend?: boolean;
  /** Shows a placeholder of the same height while data loads. */
  loading?: boolean;
  /** Accessible name of the chart (also the caption of its sr-only data table). */
  ariaLabel: string;
}

interface InternalProps<T extends object, X extends keyof T & string> extends XYChartProps<T, X> {
  kind: 'line' | 'area';
  stacked?: boolean;
}

const MARGIN = { top: 10, right: 8, bottom: 28 };
/** Longest x label before it is truncated (full text in a native tooltip). */
const MAX_X_LABEL = 120;

/** Shared engine of LineChart and AreaChart: same axes, hover model and accessibility. */
export function XYChart<T extends object, X extends keyof T & string>({
  kind,
  stacked = false,
  data,
  x,
  series,
  height = 240,
  curve: curveProp,
  valueFormatter,
  tickFormatter,
  xFormatter,
  locale: localeProp,
  xLabel,
  labels,
  showGrid = true,
  showLegend,
  loading = false,
  ariaLabel,
  className,
  style,
  ...rest
}: InternalProps<T, X>) {
  const locale = useLocale(localeProp);
  const { ref: plotRef, width } = useChartSize();
  const measure = useTextMeasure(plotRef);
  const tokens = useThemeTokens(plotRef, { '--ui-chart-curve': 'linear' });
  const svgId = useSvgId(`ui-${kind}`);
  const [focusSeries, setFocusSeries] = useState<number | null>(null);
  const n = data.length;
  const { active, keyboard, setActive, keyboardProps } = useActiveIndex(n);
  const text = { ...DEFAULT_LABELS, ...labels };

  const curve = curveProp ?? (tokens['--ui-chart-curve'] === 'monotone' ? 'monotone' : 'linear');
  const formatValue = valueFormat(valueFormatter, locale);
  const formatMaybe = (v: number | null) => (v == null ? '–' : formatValue(v));
  const colors = series.map((s, i) => seriesColor(i, s.color));

  // Geometry depends only on data and size, not on hover — keep pointer moves cheap.
  const model = useMemo(() => {
    const formatTick = axisTickFormat(valueFormatter, locale, tickFormatter, width > 0 && width < 480);
    const values = series.map((s) => data.map((row) => toNumber(row[s.key])));
    // [bottom, top] per index, or null for a missing value: the series breaks there, and
    // stacks above it rest on the series below as if it were zero.
    const bands = values.map(() => [] as ([number, number] | null)[]);
    let lo = 0;
    let hi = 0;
    for (let i = 0; i < n; i++) {
      let acc = 0;
      values.forEach((vals, s) => {
        const v = vals[i];
        if (v == null) {
          bands[s].push(null);
          return;
        }
        const bottom = stacked ? acc : 0;
        const top = bottom + v;
        if (stacked) acc = top;
        bands[s].push([bottom, top]);
        lo = Math.min(lo, top);
        hi = Math.max(hi, top);
      });
    }

    const plotH = Math.max(0, height - MARGIN.top - MARGIN.bottom);
    const axis = valueAxis(lo, hi, plotH, formatTick, measure);
    const left = Math.ceil(axis.labelWidth) + AXIS_GAP + 2;
    const plotW = Math.max(0, width - left - MARGIN.right);
    const xs = scaleLinear([0, Math.max(0, n - 1)], [0, plotW]);
    const ys = scaleLinear(axis.domain, [plotH, 0]);

    const paths = bands.map((band) => {
      const runs = splitDefined(band.map((b, i) => b && { top: [xs(i), ys(b[1])] as Point, bottom: [xs(i), ys(b[0])] as Point }));
      const long = runs.filter((run) => run.length > 1);
      return {
        line: long.map((run) => linePath(run.map((p) => p.top), curve)).join(''),
        area: kind === 'area' ? long.map((run) => areaPath(run.map((p) => p.top), run.map((p) => p.bottom), curve)).join('') : '',
        // A value between two gaps has no neighbour to connect to: mark it with a dot.
        points: runs.filter((run) => run.length === 1).map((run) => run[0].top),
      };
    });

    const xText = (i: number) => (xFormatter ? xFormatter(data[i][x], i) : formatAxisValue(data[i][x], locale));
    const categories = data.map((_, i) => {
      const full = xText(i);
      return { index: i, position: xs(i), label: truncateText(full, MAX_X_LABEL, measure), title: full };
    });
    const titles = data.map((row, i) => (xFormatter ? xFormatter(row[x], i) : formatTitleValue(row[x], locale)));
    const hasData = values.some((vals) => vals.some((v) => v != null));
    return { values, bands, axis, left, plotW, plotH, xs, ys, paths, categories, titles, hasData };
  }, [data, series, x, n, stacked, kind, height, width, curve, measure, valueFormatter, tickFormatter, xFormatter, locale]);

  const { values, bands, axis, left, plotW, plotH, xs, ys, paths, categories, titles, hasData } = model;

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (n === 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setActive(clamp(Math.round(xs.invert(event.clientX - rect.left - left)), 0, n - 1));
  };

  const rowsAt = (i: number) => [
    ...series.map((s, si) => ({ label: s.label, color: colors[si], value: formatMaybe(values[si][i]) })),
    ...(stacked && series.length > 1
      ? [{ label: text.total, value: formatValue(values.reduce((sum, vals) => sum + (vals[i] ?? 0), 0)) }]
      : []),
  ];
  const activeRows = active != null ? rowsAt(active) : null;

  const legendOn = showLegend ?? series.length > 1;
  const root = `ui-${kind}-chart`;
  const ready = width > 0 && !loading;

  return (
    <div
      {...rest}
      className={cx('ui-chart', root, className)}
      style={{ ...cssVars({ '--_glow': `url(#${svgId}-glow)` }), ...style }}
      data-stacked={stacked || undefined}
      aria-busy={loading || undefined}
    >
      <div ref={plotRef} className="ui-chart__plot" style={{ height }}>
        {loading && <Skeleton className="ui-chart__skeleton" height={height} />}
        {ready && (
          <svg
            className="ui-chart__svg"
            width={width}
            height={height}
            role="img"
            aria-label={ariaLabel}
            onPointerMove={onPointerMove}
            onPointerDown={onPointerMove}
            onPointerLeave={(event) => event.pointerType === 'mouse' && setActive(null)}
            {...keyboardProps}
          >
            <defs>
              <GlowFilter id={`${svgId}-glow`} />
              {kind === 'area' &&
                series.map((s, i) => (
                  <linearGradient key={s.key} id={`${svgId}-fill-${i}`} x1="0" y1="0" x2="0" y2="1" style={cssVars({ '--_color': colors[i] })}>
                    <stop offset="0%" className={`${root}__stop`} data-pos="start" />
                    <stop offset="100%" className={`${root}__stop`} data-pos="end" />
                  </linearGradient>
                ))}
            </defs>
            <g transform={`translate(${left},${MARGIN.top})`}>
              {hasData && (
                <>
                  <ValueGrid ticks={axis.ticks} labels={axis.labels} scale={ys} length={plotW} orientation="horizontal" showGrid={showGrid} />
                  <CategoryLabels items={categories} width={plotW} y={plotH + AXIS_GAP + 12} measure={measure} />
                </>
              )}
              {hasData &&
                series.map((s, si) => (
                  <g
                    key={s.key}
                    className={`${root}__series`}
                    style={cssVars({ '--_color': colors[si] })}
                    data-dimmed={focusSeries != null && focusSeries !== si ? '' : undefined}
                  >
                    {paths[si].area && <path className={`${root}__area`} fill={`url(#${svgId}-fill-${si})`} d={paths[si].area} />}
                    {paths[si].line && <path className="ui-chart__line" d={paths[si].line} pathLength={1} />}
                    {paths[si].points.map(([px, py]) => (
                      <circle key={px} className="ui-chart__point" cx={px} cy={py} r={2.5} />
                    ))}
                  </g>
                ))}
              {active != null && (
                // Placed with CSS transforms (the line on a crisp pixel), so moving between points glides
                // with the tooltip instead of jumping; a fresh hover just appears.
                <g className="ui-chart__hover" data-glide="" aria-hidden="true" style={{ transform: `translateX(${crisp(xs(active))}px)` }}>
                  <line className="ui-chart__crosshair" x1={0} x2={0} y1={0} y2={plotH} />
                  {series.map((s, si) => {
                    const band = bands[si][active];
                    return band ? (
                      <g key={s.key} className="ui-chart__dot-at" style={{ transform: `translateY(${ys(band[1])}px)` }}>
                        <circle className="ui-chart__dot" cx={0} cy={0} r={4} style={cssVars({ '--_color': colors[si] })} />
                      </g>
                    ) : null;
                  })}
                </g>
              )}
            </g>
          </svg>
        )}
        {ready && !hasData && <ChartEmpty>{text.empty}</ChartEmpty>}
        {ready && active != null && activeRows && (
          <ChartTooltip
            x={left + xs(active)}
            y={MARGIN.top}
            containerWidth={width}
            containerHeight={height}
            title={titles[active]}
            rows={activeRows}
          />
        )}
        <ChartAnnouncer text={keyboard && active != null && activeRows ? describeRow(titles[active], activeRows) : null} />
      </div>
      {legendOn && (
        <ChartLegend
          items={series.map((s, i) => ({ label: s.label, color: colors[i] }))}
          activeIndex={focusSeries}
          onActiveIndexChange={setFocusSeries}
        />
      )}
      <ChartDataTable
        caption={ariaLabel}
        columns={[xLabel ?? x, ...series.map((s) => s.label)]}
        rows={data.map((_, i) => [titles[i], ...values.map((vals) => formatMaybe(vals[i]))])}
      />
    </div>
  );
}
