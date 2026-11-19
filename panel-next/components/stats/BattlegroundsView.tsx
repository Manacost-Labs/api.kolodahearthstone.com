'use client';
import { type ReactNode, useCallback, useMemo, useState } from 'react';
import { formatNumber, formatPercent } from '@/lib/format';
import { Time } from '../ui';
import { ScatterChart, type ScatterSeries } from '../varylo-ui/charts/ScatterChart';
import type { ModuleViewProps } from './ModuleView';
import { dataAsOf, text, toNumber } from './view-utils';
import './battlegrounds-view.css';

const TOP_HEROES = 10;
const TOP_MINIONS = 12;
const TIERS = ['A', 'B', 'C', 'D'] as const;
const placeFormat = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const place = (value: number) => placeFormat.format(value);

/**
 * Art with a placeholder under it: the name stays visible while the file loads (the cold S3 mount can take
 * a second) and when it is missing, so there is never an empty square.
 */
function ArtTile({
  src,
  label,
  hint,
  variant,
}: {
  src: string;
  label: string;
  hint?: string;
  variant: string;
}) {
  const [state, setState] = useState<'loading' | 'ready' | 'broken'>('loading');
  // A cached image can finish before React attaches onLoad.
  const ref = useCallback((node: HTMLImageElement | null) => {
    if (node?.complete) setState(node.naturalWidth > 0 ? 'ready' : 'broken');
  }, []);
  return (
    <span className={`bg-art bg-art--${variant}`} data-state={src ? state : 'broken'}>
      <span className="bg-art__ph" aria-hidden="true">
        <b>{label}</b>
        {hint ? <small>{hint}</small> : null}
      </span>
      {src && state !== 'broken' ? (
        <img
          ref={ref}
          src={src}
          alt={label}
          decoding="async"
          onLoad={() => setState('ready')}
          onError={() => setState('broken')}
        />
      ) : null}
    </span>
  );
}

type Hero = { name: string; tier: string; share: number; place: number; games: number; image: string };

function readHeroes(rows: ModuleViewProps['data']['rows']): Hero[] {
  return (rows ?? []).flatMap(row => {
    const share = toNumber(row.pick_rate_value ?? row.share_pct ?? row.pick_rate);
    const average = toNumber(row.avg_placement_value ?? row.avg_placement);
    const name = text(row.hero) || text(row.name);
    if (share === null || average === null || !name) return [];
    return [
      {
        name,
        tier: text(row.tier).toUpperCase(),
        share,
        place: average,
        games: toNumber(row.games) ?? 0,
        image: text(row.image_url),
      },
    ];
  });
}

function Head({ title, note, asOf }: { title: string; note?: ReactNode; asOf?: string | null }) {
  return (
    <header className="bg-view__head">
      <h3>{title}</h3>
      <span className="bg-view__note">
        {note}
        {asOf ? (
          <>
            {note ? ' · ' : ''}
            Данные на <Time value={asOf} />
          </>
        ) : null}
      </span>
    </header>
  );
}

export function BgHeroesView({ data }: ModuleViewProps) {
  const model = useMemo(() => {
    const heroes = readHeroes(data.rows);
    const series: (ScatterSeries<Hero> & { tier: string })[] = [...TIERS, ''].flatMap(tier => {
      const points = heroes.filter(hero =>
        tier ? hero.tier === tier : !(TIERS as readonly string[]).includes(hero.tier),
      );
      return points.length
        ? [
            {
              tier,
              label: tier ? `Тир ${tier}` : 'Без тира',
              color: `var(--bg-tier-${tier || 'none'})`,
              data: points,
            },
          ]
        : [];
    });
    const top = [...heroes].sort((a, b) => b.share - a.share).slice(0, TOP_HEROES);
    return { heroes, series, top };
  }, [data.rows]);

  if (model.heroes.length === 0) return null;
  const asOf = dataAsOf(data.summary);
  const widest = Math.max(...model.top.map(hero => hero.share), 1);

  return (
    <section className="bg-view bg-view--heroes" aria-label="Поля сражений: топ героев и график">
      <div className="bg-view__card">
        <Head title={`Топ-${model.top.length} по выбору`} asOf={asOf} />
        <ol className="bg-top">
          {model.top.map((hero, index) => (
            <li key={`${hero.name}|${hero.share}`} className="bg-top__row">
              <span className="bg-top__rank">{index + 1}</span>
              <ArtTile src={hero.image} label={hero.name} variant="hero" />
              <span className="bg-top__name">
                <b title={hero.name}>{hero.name}</b>
                {hero.tier ? <small data-tier={hero.tier}>Тир {hero.tier}</small> : null}
              </span>
              <span className="bg-top__bar" aria-hidden="true">
                <i style={{ width: `${(hero.share / widest) * 100}%` }} />
              </span>
              <span className="bg-top__num" title="Выбор: доля предложений, которые взяли">
                {formatPercent(hero.share)}
              </span>
              <span className="bg-top__num bg-top__place" title="Среднее место">
                {place(hero.place)}
              </span>
            </li>
          ))}
        </ol>
        <p className="bg-top__legend">Выбор · среднее место (меньше — лучше)</p>
      </div>
      <div className="bg-view__card bg-view__chart">
        <Head
          title="Выбор × среднее место"
          note={`Меньше место — лучше · размер точки — игры · ${formatNumber(model.heroes.length)} героев`}
        />
        <ScatterChart
          series={model.series}
          x="share"
          y="place"
          size="games"
          name="name"
          xLabel="Выбор, %"
          yLabel="Среднее место"
          sizeLabel="Игры"
          xFormatter={formatPercent}
          yFormatter={place}
          sizeFormatter={formatNumber}
          height={400}
          maxBubbleSize={16}
          ariaLabel="Выбор и среднее место героев, цвет — тир, размер — число игр"
          labels={{ empty: 'Нет данных', series: 'Тир', name: 'Герой' }}
        />
      </div>
    </section>
  );
}

type Minion = {
  id: string;
  name: string;
  tier: number;
  popularity: number | null;
  winrate: number | null;
  place: number | null;
  image: string;
};

export function BgMinionsView({ data }: ModuleViewProps) {
  const model = useMemo(() => {
    const minions: Minion[] = (data.rows ?? []).flatMap(row => {
      const tier = toNumber(row.tavern_tier);
      const name = text(row.name_ru) || text(row.name) || text(row.minion);
      if (tier === null || !name) return [];
      return [
        {
          id: text(row.card_id) || name,
          name,
          tier,
          popularity: toNumber(row.popularity_value ?? row.popularity),
          winrate: toNumber(row.combat_winrate_value ?? row.combat_winrate),
          place: toNumber(row.avg_placement_with),
          image: text(row.image_url),
        },
      ];
    });
    const counts = new Map<number, number>();
    for (const minion of minions) counts.set(minion.tier, (counts.get(minion.tier) ?? 0) + 1);
    const tiers = [...counts.entries()].sort((a, b) => a[0] - b[0]);
    const top = [...minions]
      .filter(minion => minion.popularity !== null)
      .sort((a, b) => (b.popularity ?? 0) - (a.popularity ?? 0))
      .slice(0, TOP_MINIONS);
    return { total: minions.length, tiers, top };
  }, [data.rows]);

  if (model.total === 0) return null;
  const asOf = dataAsOf(data.summary);

  return (
    <section className="bg-view bg-view--minions" aria-label="Поля сражений: самые популярные существа">
      <div className="bg-view__card">
        <Head
          title={`Топ-${model.top.length} по популярности`}
          note={
            <span className="bg-tiers" role="group" aria-label="Существ по уровням таверны">
              {model.tiers.map(([tier, count]) => (
                <span
                  key={tier}
                  className="bg-tier-chip"
                  data-tier={tier}
                  title={`Таверна ${tier}: ${count}`}
                >
                  {tier}
                  <small>{formatNumber(count)}</small>
                </span>
              ))}
            </span>
          }
          asOf={asOf}
        />
        <ol className="bg-cards">
          {model.top.map(minion => (
            <li key={minion.id} className="bg-card">
              <ArtTile
                src={minion.image}
                label={minion.name}
                hint={`Таверна ${minion.tier}`}
                variant="minion"
              />
              <span
                className="bg-tier-chip bg-card__tier"
                data-tier={minion.tier}
                title={`Таверна ${minion.tier}`}
              >
                {minion.tier}
              </span>
              <b className="bg-card__name" title={minion.name}>
                {minion.name}
              </b>
              <span className="bg-card__stats">
                <span title="Популярность">
                  {minion.popularity === null ? '—' : formatPercent(minion.popularity)}
                </span>
                <span title="Винрейт боя" data-up={(minion.winrate ?? 0) >= 50 || undefined}>
                  {minion.winrate === null ? '—' : formatPercent(minion.winrate)}
                </span>
                <span title="Среднее место">{minion.place === null ? '—' : place(minion.place)}</span>
              </span>
            </li>
          ))}
        </ol>
        <p className="bg-top__legend">Популярность · винрейт боя · среднее место с существом</p>
      </div>
    </section>
  );
}
