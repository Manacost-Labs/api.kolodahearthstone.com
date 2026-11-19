'use client';
import { useMemo } from 'react';
import { archetypeIdentity, classSlugs } from '@/lib/archetypes';
import { classLabel, formatNumber, formatPercent } from '@/lib/format';
import { ClassIcon } from '../ClassIcon';
import { Time } from '../ui';
import { ScatterChart, type ScatterSeries } from '../varylo-ui/charts/ScatterChart';
import type { ModuleViewProps } from './ModuleView';
import './meta-view.css';

type Point = { name: string; en: string; share: number; winrate: number; games: number };

// The two meta modules name the share differently.
const shareField: Record<string, string> = { meta: 'popularity', hsguru_archetypes: 'popularity_pct' };
const TOP = 10;
const toNumber = (value: unknown): number | null => {
  const number = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return typeof number === 'number' && Number.isFinite(number) ? number : null;
};
const percent = (value: number) => formatPercent(value);

export function MetaView({ module, data }: ModuleViewProps) {
  const field = shareField[module] ?? 'popularity';
  const model = useMemo(() => {
    const rows = (data.rows ?? []).flatMap(row => {
      const share = toNumber(row[field]);
      const winrate = toNumber(row.winrate);
      if (share === null || winrate === null) return [];
      const identity = archetypeIdentity(row.archetype, row.class_name);
      const games = toNumber(row.games) ?? 0;
      return [
        {
          identity,
          point: { name: identity.title, en: identity.subtitle ?? '', share, winrate, games } as Point,
        },
      ];
    });
    const groups = new Map<string, Point[]>();
    for (const { identity, point } of rows) {
      const key = identity.classSlug ?? 'neutral';
      groups.set(key, [...(groups.get(key) ?? []), point]);
    }
    const series: (ScatterSeries<Point> & { slug: string })[] = [...classSlugs, 'neutral' as const]
      .filter(slug => groups.has(slug))
      .map(slug => ({
        slug,
        label: slug === 'neutral' ? 'Класс не определён' : classLabel(slug),
        color: `var(--meta-class-${slug})`,
        data: groups.get(slug) ?? [],
      }));
    const top = [...rows].sort((a, b) => b.point.share - a.point.share).slice(0, TOP);
    const withClass = rows.filter(item => item.identity.classSlug).length;
    return { series, top, total: rows.length, withClass };
  }, [data.rows, field]);

  if (model.total === 0) return null;
  const asOf = data.summary?.find(item => item.key === 'data_as_of')?.value;
  const widest = Math.max(...model.top.map(item => item.point.share), 1);

  return (
    <section className="meta-view" aria-label="Мета: топ архетипов и график">
      <div className="meta-view__top">
        <header className="meta-view__head">
          <h3>Топ-{model.top.length} по доле</h3>
          {asOf ? (
            <span className="meta-view__asof">
              Данные на <Time value={asOf} />
            </span>
          ) : null}
        </header>
        <ol className="meta-top">
          {model.top.map(({ identity, point }, index) => (
            <li key={`${point.name}|${point.en}|${point.share}`} className="meta-top__row">
              <span className="meta-top__rank">{index + 1}</span>
              <ClassIcon slug={identity.classSlug} size={40} className="meta-top__icon" />
              <span className="meta-top__name">
                <b title={identity.title}>{identity.title}</b>
                {identity.subtitle ? <small lang="en">{identity.subtitle}</small> : null}
              </span>
              <span className="meta-top__bar" aria-hidden="true">
                <i
                  style={{
                    width: `${(point.share / widest) * 100}%`,
                    background: `var(--meta-class-${identity.classSlug ?? 'neutral'})`,
                  }}
                />
              </span>
              <span className="meta-top__num" title="Доля меты">
                {percent(point.share)}
              </span>
              <span
                className="meta-top__num meta-top__wr"
                title="Винрейт"
                data-up={point.winrate >= 50 || undefined}
              >
                {percent(point.winrate)}
              </span>
              <span className="meta-top__games" title="Игры">
                {formatNumber(point.games)}
              </span>
            </li>
          ))}
        </ol>
        <p className="meta-top__legend">Доля меты · винрейт · игры</p>
      </div>
      <div className="meta-view__chart">
        <header className="meta-view__head">
          <h3>Доля × винрейт</h3>
          <span className="meta-view__asof">
            Размер точки — число игр · иконка у {formatNumber(model.withClass)} из {formatNumber(model.total)}
          </span>
        </header>
        <ScatterChart
          series={model.series}
          x="share"
          y="winrate"
          size="games"
          name="name"
          xLabel="Доля меты, %"
          yLabel="Винрейт, %"
          sizeLabel="Игры"
          xFormatter={percent}
          yFormatter={percent}
          sizeFormatter={formatNumber}
          height={400}
          maxBubbleSize={20}
          ariaLabel="Доля меты и винрейт архетипов, цвет — класс, размер — число игр"
          labels={{ empty: 'Нет данных', series: 'Класс', name: 'Архетип' }}
        />
      </div>
    </section>
  );
}
