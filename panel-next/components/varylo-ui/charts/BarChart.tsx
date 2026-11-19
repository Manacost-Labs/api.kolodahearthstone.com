'use client';

import { useMemo, useState, type ComponentPropsWithRef, type PointerEvent } from 'react';
import { cx } from '../primitives';
import {
  AXIS_GAP,
  BarsSkeleton,
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
import { barPath, niceTicks, placeLabels, scaleBand, scaleLinear, type BarEdge } from './scale';
import { useChartSize } from './useChartSize';
import {
  capsuleRadius,
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
import type { XYChartLabels } from './XYChart';
import { useLocale } from '../theme/ThemeProvider';
import { tokenNumber, useThemeTokens } from '../theme/useThemeTokens';
import './Chart.css';
import './BarChart.css';

export type BarChartLabels = XYChartLabels;

const DEFAULT_LABELS: BarChartLabels = { empty: 'No data', total: 'Total' };

export interface BarChartProps<T extends object, X extends keyof T & string = keyof T & string>
  extends Omit<ComponentPropsWithRef<'div'>, 'children'> {
  /** Rows to plot, one bar group per row; a missing or non-numeric value draws no bar. */
  data: T[];
  /** Category field (one group of bars per row). */
  x: X;
  /** Numeric fields drawn as bars, each with its legend label and optional color. */
  series: ChartSeries<T>[];
  /** `vertical`: columns. `horizontal`: rows of bars with category labels on the left. */
  orientation?: 'vertical' | 'horizontal';
  /** Stack series instead of placing them side by side. */
  stacked?: boolean;
  /** Print values at the end of each bar (stack totals when stacked); hidden where they don't fit. */
  showValues?: boolean;
  /** Default 240 for columns; horizontal charts grow with the number of rows. */
  height?: number;
  /** Corner radius of the bar end. Default: the theme's `--ui-bar-chart-bar-radius` (2px in Vercel, capsule in Glass). */
  radius?: number;
  /** Formats values in the tooltip, bar labels and data table. Axis ticks keep its units around a shorter number ("$15K"). */
  valueFormatter?: ValueFormatter;
  /** Formats the value-axis ticks instead of the shortened `valueFormatter`. */
  tickFormatter?: ValueFormatter;
  /** Formats category values for axis labels and the tooltip title. */
  xFormatter?: (value: T[X], index: number) => string;
  /** BCP 47 locale for the default number and date formatting. Default: the `ThemeProvider` locale, then the browser's. */
  locale?: string;
  /** Header of the category column in the accessible data table. Default: the `x` key. */
  xLabel?: string;
  /** Overrides for the built-in strings. */
  labels?: Partial<BarChartLabels>;
  /** Grid lines at the value ticks; the zero line is drawn either way. */
  showGrid?: boolean;
  /** Default: shown when there is more than one series. */
  showLegend?: boolean;
  /** Shows a placeholder of the same height while data loads. */
  loading?: boolean;
  /** Accessible name of the chart (also the caption of its sr-only data table). */
  ariaLabel: string;
}

interface Segment {
  from: number;
  to: number;
  /** Outermost segment of its stack direction — the only one with a rounded end. */
  outer: boolean;
}

const VALUE_PAD = 6;
/** Hairline between stacked segments so neighbouring colors stay distinct. */
const STACK_GAP = 1;
const CATEGORY_FONT = { size: 12 };
const MAX_CATEGORY = 160;
/** Thickest bar in px (side by side / alone): wide charts get air between groups instead of bricks. */
const MAX_BAR = { grouped: 24, single: 40 };
/** Horizontal bars in a narrow chart put their category on a line of its own above the bar. */
const COMPACT_WIDTH = 480;
const LABEL_ROW = 18;

export function BarChart<T extends object, X extends keyof T & string>({
  data,
  x,
  series,
  orientation = 'vertical',
  stacked = false,
  showValues = false,
  height: heightProp,
  radius: radiusProp,
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
}: BarChartProps<T, X>) {
  const locale = useLocale(localeProp);
  const { ref: plotRef, width } = useChartSize();
  const measure = useTextMeasure(plotRef);
  const tokens = useThemeTokens(plotRef, { '--ui-chart-capsule': '0', '--ui-bar-chart-bar-radius': '2px', '--ui-bar-chart-hover-radius': '4px' });
  const svgId = useSvgId('ui-bar');
  const [focusSeries, setFocusSeries] = useState<number | null>(null);
  const n = data.length;
  const k = series.length;
  const { active, keyboard, setActive, keyboardProps } = useActiveIndex(n);
  const horizontal = orientation === 'horizontal';
  const compact = horizontal && width > 0 && width < COMPACT_WIDTH;
  const grouped = !stacked && k > 1;
  const text = { ...DEFAULT_LABELS, ...labels };

  const formatValue = valueFormat(valueFormatter, locale);
  const formatMaybe = (v: number | null) => (v == null ? '–' : formatValue(v));
  const colors = series.map((s, i) => seriesColor(i, s.color));

  const model = useMemo(() => {
    const formatTick = axisTickFormat(valueFormatter, locale, tickFormatter, width > 0 && width < 480);
    const names = data.map((row, i) => (xFormatter ? xFormatter(row[x], i) : formatAxisValue(row[x], locale)));
    const titles = data.map((row, i) => (xFormatter ? xFormatter(row[x], i) : formatTitleValue(row[x], locale)));

    // Positive and negative values stack away from zero independently.
    const values = series.map((s) => data.map((row) => toNumber(row[s.key])));
    const segments: Segment[][] = series.map(() => []);
    const ends: number[] = [];
    let lo = 0;
    let hi = 0;
    for (let i = 0; i < n; i++) {
      let pos = 0;
      let neg = 0;
      let lastPos = -1;
      let lastNeg = -1;
      values.forEach((vals, s) => {
        const v = vals[i] ?? 0;
        let from = 0;
        if (stacked) {
          from = v >= 0 ? pos : neg;
          if (v >= 0) pos += v;
          else neg += v;
        }
        if (v > 0) lastPos = s;
        if (v < 0) lastNeg = s;
        segments[s].push({ from, to: from + v, outer: !stacked });
        lo = Math.min(lo, from + v);
        hi = Math.max(hi, from + v);
      });
      if (stacked) {
        if (lastPos >= 0) segments[lastPos][i].outer = true;
        if (lastNeg >= 0) segments[lastNeg][i].outer = true;
      }
      ends.push(stacked ? pos : Math.max(0, ...values.map((vals) => vals[i] ?? 0)));
    }
    const totals = data.map((_, i) => values.reduce((sum, vals) => sum + (vals[i] ?? 0), 0));
    const valueLabels = showValues ? (stacked ? [totals.map(formatValue)] : values.map((vals) => vals.map(formatMaybe))) : [];
    const valueLabelWidth = valueLabels.flat().reduce((m, l) => Math.max(m, measure(l)), 0);

    // Layout. Horizontal charts derive their height from the row count unless given one. Compact
    // rows put the category above the bar; with values printed on the bars they need no axis.
    const labelRow = compact ? LABEL_ROW : 0;
    const axisless = compact && showValues;
    const rowSize = (grouped ? 12 * k + 14 : compact ? 24 : 32) + labelRow;
    const top = !horizontal && showValues ? 22 : 10;
    const bottom = axisless ? 4 : 28;
    const height = heightProp ?? (horizontal ? Math.max(120, n * rowSize + top + bottom) : 240);
    const plotH = Math.max(0, height - top - bottom);
    const widestName = names.reduce((m, l) => Math.max(m, measure(l, horizontal ? CATEGORY_FONT : undefined)), 0);

    let left: number;
    let right: number;
    let axis: ReturnType<typeof valueAxis>;
    if (horizontal) {
      left = compact ? 0 : Math.ceil(Math.min(widestName, width * 0.32, MAX_CATEGORY)) + AXIS_GAP;
      // The last tick label is centred on the plot edge, so half of it needs room too.
      const lastTick = formatTick(hi, niceTicks(lo, hi, 4).step, Math.max(Math.abs(lo), Math.abs(hi)));
      right = Math.ceil(Math.max(showValues ? valueLabelWidth + VALUE_PAD + 4 : 0, axisless ? 0 : measure(lastTick) / 2 + 4));
      axis = valueAxis(lo, hi, Math.max(0, width - left - right), formatTick, measure, true);
    } else {
      axis = valueAxis(lo, hi, plotH, formatTick, measure);
      left = Math.ceil(axis.labelWidth) + AXIS_GAP + 2;
      right = 8;
    }
    const plotW = Math.max(0, width - left - right);
    const band = scaleBand(n, [0, horizontal ? plotH : plotW], compact ? 0.18 : grouped ? 0.24 : 0.32);
    const vs = scaleLinear(axis.domain, horizontal ? [0, plotW] : [plotH, 0]);
    const room = Math.max(1, band.bandwidth - labelRow);
    const gap = grouped ? Math.min(4, room * 0.08) : 0;
    const barSize = Math.min(grouped ? MAX_BAR.grouped : MAX_BAR.single, grouped ? Math.max(1, (room - gap * (k - 1)) / k) : room);
    // Where the bars of a row or column start: under the label row, or centred in a band wider than they need.
    const inset = labelRow + (compact ? 0 : (room - (barSize * (grouped ? k : 1) + gap * (grouped ? k - 1 : 0))) / 2);

    const item = (name: string, i: number, max: number) => ({
      index: i,
      position: band.center(i),
      label: truncateText(name, max, measure, horizontal ? CATEGORY_FONT : undefined),
      title: name,
    });
    const fitting = (items: ReturnType<typeof item>[]) => placeLabels(items.map((c) => ({ position: c.position, width: measure(c.label) })), plotW, 12).length;
    let categories = names.map((name, i) => item(name, i, compact ? width - 4 : horizontal ? left - AXIS_GAP : MAX_CATEGORY));
    if (!horizontal) {
      // Columns: whole labels, thinned to the ones that fit ("22:00", "02:00"…). Cut to a column's
      // width only when that keeps more of them and every cut one still reads ("/docs/getti…", not "22…").
      const whole = fitting(categories);
      if (whole < n) {
        const cut = names.map((name, i) => item(name, i, Math.max(band.step - 4, 24)));
        const readable = cut.every((c) => c.label === c.title || Array.from(c.label).length > 6);
        if (readable && fitting(cut) > whole) categories = cut;
      }
    }
    const hasData = values.some((vals) => vals.some((v) => v != null));
    return { names, titles, values, segments, ends, totals, height, top, left, right, plotW, plotH, axis, axisless, band, vs, gap, barSize, inset, categories, hasData };
  }, [data, series, x, n, k, stacked, grouped, horizontal, compact, showValues, heightProp, width, measure, valueFormatter, tickFormatter, xFormatter, locale]);

  const { titles, values, segments, ends, totals, height, top, left, right, plotW, plotH, axis, axisless, band, vs, gap, barSize, inset, categories, hasData } = model;
  const radius = radiusProp ?? capsuleRadius(tokenNumber(tokens['--ui-bar-chart-bar-radius']), barSize, tokenNumber(tokens['--ui-chart-capsule']) > 0);

  const bar = (s: number, i: number) => {
    const seg = segments[s][i];
    const offset = grouped ? s * (barSize + gap) : 0;
    let p0 = vs(seg.from);
    const p1 = vs(seg.to);
    // Stacked segments leave a hairline towards the one they rest on.
    if (stacked && seg.from !== 0 && Math.abs(p1 - p0) > STACK_GAP * 2) p0 += Math.sign(p1 - p0) * STACK_GAP;
    const positive = seg.to >= seg.from;
    const edge: BarEdge = horizontal ? (positive ? 'right' : 'left') : positive ? 'top' : 'bottom';
    const lead = band.start(i) + inset + offset;
    const geometry = horizontal
      ? { x: Math.min(p0, p1), y: lead, w: Math.abs(p1 - p0), h: barSize }
      : { x: lead, y: Math.min(p0, p1), w: barSize, h: Math.abs(p1 - p0) };
    return { edge, d: barPath(geometry.x, geometry.y, geometry.w, geometry.h, seg.outer ? radius : 0, edge) };
  };

  // Value labels that are wider than their bar (vertical) would collide with neighbours: skip them.
  const valueMarks = (i: number) =>
    (stacked
      ? [{ key: 'total', end: vs(ends[i]), size: barSize, offset: 0, label: formatValue(totals[i]), negative: false }]
      : series.map((s, si) => ({
          key: s.key,
          end: vs(segments[si][i].to),
          size: barSize,
          offset: grouped ? si * (barSize + gap) : 0,
          label: formatMaybe(values[si][i]),
          negative: segments[si][i].to < 0,
        }))
    ).filter((v) => horizontal || measure(v.label) <= v.size + (grouped ? gap : band.step - barSize) - 2);

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (n === 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setActive(band.indexAt(horizontal ? event.clientY - rect.top - top : event.clientX - rect.left - left));
  };

  const rowsAt = (i: number) => [
    ...series.map((s, si) => ({ label: s.label, color: colors[si], value: formatMaybe(values[si][i]) })),
    ...(stacked && k > 1 ? [{ label: text.total, value: formatValue(totals[i]) }] : []),
  ];
  const activeRows = active != null ? rowsAt(active) : null;
  const legendOn = showLegend ?? k > 1;
  const ready = width > 0 && !loading;

  return (
    <div
      {...rest}
      className={cx('ui-chart ui-bar-chart', className)}
      style={{ ...cssVars({ '--_glow': `url(#${svgId}-glow)` }), ...style }}
      data-orientation={orientation}
      aria-busy={loading || undefined}
    >
      <div ref={plotRef} className="ui-chart__plot" style={{ height }}>
        {loading && <BarsSkeleton horizontal={horizontal} />}
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
            data-compact={compact || undefined}
            {...keyboardProps}
          >
            <defs>
              <GlowFilter id={`${svgId}-glow`} blur={4} dy={2} opacity={0.3} />
            </defs>
            <g transform={`translate(${left},${top})`}>
              {active != null && (
                // Moved with a CSS transform, so it glides from category to category; a fresh hover just appears.
                <rect
                  className="ui-bar-chart__hover-band"
                  rx={tokenNumber(tokens['--ui-bar-chart-hover-radius'])}
                  {...(horizontal ? { x: -4, y: 0, width: plotW + 8, height: band.step } : { x: 0, y: -4, width: band.step, height: plotH + 8 })}
                  style={{ transform: horizontal ? `translateY(${active * band.step}px)` : `translateX(${active * band.step}px)` }}
                />
              )}
              {hasData && !axisless && (
                <ValueGrid
                  ticks={axis.ticks}
                  labels={axis.labels}
                  scale={vs}
                  length={horizontal ? plotH : plotW}
                  orientation={horizontal ? 'vertical' : 'horizontal'}
                  showGrid={showGrid}
                  extent={plotW + right - 1}
                  measure={measure}
                />
              )}
              {hasData && axisless && (
                <line className="ui-chart__grid" data-zero x1={crisp(vs(0))} x2={crisp(vs(0))} y1={0} y2={plotH} aria-hidden="true" />
              )}
              {hasData && horizontal && (
                <g className="ui-chart__category-axis" aria-hidden="true">
                  {categories.map((c) => (
                    <text
                      key={c.index}
                      className="ui-chart__axis-label ui-bar-chart__category"
                      {...(compact
                        ? { x: 0, y: band.start(c.index) + LABEL_ROW - 6, textAnchor: 'start' as const }
                        : { x: -AXIS_GAP, y: c.position, dy: '0.32em', textAnchor: 'end' as const })}
                    >
                      {c.label !== c.title && <title>{c.title}</title>}
                      {c.label}
                    </text>
                  ))}
                </g>
              )}
              {hasData && !horizontal && <CategoryLabels items={categories} width={plotW} y={plotH + AXIS_GAP + 12} measure={measure} />}
              {hasData &&
                data.map((_, i) => (
                  <g
                    key={i}
                    className="ui-bar-chart__group"
                    style={cssVars({ '--_i': i })}
                    data-dimmed={active != null && active !== i ? '' : undefined}
                  >
                    {series.map((s, si) => {
                      const b = bar(si, i);
                      if (!b.d) return null;
                      return (
                        <path
                          key={s.key}
                          className="ui-bar-chart__bar"
                          d={b.d}
                          data-edge={b.edge}
                          data-dimmed={focusSeries != null && focusSeries !== si ? '' : undefined}
                          style={cssVars({ '--_color': colors[si] })}
                        />
                      );
                    })}
                    {showValues &&
                      valueMarks(i).map((v) => {
                        const across = band.start(i) + inset + v.offset + v.size / 2;
                        return horizontal ? (
                          <text
                            key={v.key}
                            className="ui-bar-chart__value"
                            x={v.negative ? v.end - VALUE_PAD : v.end + VALUE_PAD}
                            y={across}
                            dy="0.32em"
                            textAnchor={v.negative ? 'end' : 'start'}
                          >
                            {v.label}
                          </text>
                        ) : (
                          <text
                            key={v.key}
                            className="ui-bar-chart__value"
                            x={across}
                            y={v.negative ? v.end + VALUE_PAD + 9 : v.end - VALUE_PAD}
                            textAnchor="middle"
                          >
                            {v.label}
                          </text>
                        );
                      })}
                  </g>
                ))}
            </g>
          </svg>
        )}
        {ready && !hasData && <ChartEmpty>{text.empty}</ChartEmpty>}
        {ready && active != null && activeRows && (
          <ChartTooltip
            {...(horizontal
              ? { x: left + vs(ends[active]), y: top + band.start(active) }
              : { x: left + band.center(active), y: top, anchorWidth: band.bandwidth })}
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
