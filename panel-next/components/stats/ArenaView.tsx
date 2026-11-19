'use client';
import { useMemo } from 'react';
import { type ClassSlug, normalizeClass } from '@/lib/archetypes';
import { classLabel, formatPercent } from '@/lib/format';
import { ClassIcon } from '../ClassIcon';
import { Time } from '../ui';
import type { ModuleViewProps } from './ModuleView';
import { dataAsOf, text, toNumber } from './view-utils';
import './arena-view.css';

const LABEL_ROOM = 24; // px above and below the bars for the value labels
const PLOT_BARS = 212; // px for the bars between the labels (the plot is 260 px)

type ArenaClass = {
  slug: ClassSlug | null;
  name: string;
  winrate: number;
  pick: number | null;
  drafts: number | null;
};

/** Columns of the win rate of every Arena class around 50 %: up is better than a coin flip, down is worse. */
export function ArenaView({ data }: ModuleViewProps) {
  const model = useMemo(() => {
    const classes: ArenaClass[] = (data.rows ?? []).flatMap(row => {
      const winrate = toNumber(row.win_rate ?? row.winrate);
      if (winrate === null) return [];
      const slug = normalizeClass(row.class_slug ?? row.slug ?? row.class_name ?? row.class);
      const name = text(row.class_ru) || (slug ? classLabel(slug) : text(row.class_name) || text(row.class));
      return [
        { slug, name: name || '—', winrate, pick: toNumber(row.pick_rate), drafts: toNumber(row.num_drafts) },
      ];
    });
    classes.sort((a, b) => b.winrate - a.winrate);
    // The scale is symmetric around 50 % and never tighter than ±5 points, so a quiet day is not stretched.
    const span = Math.max(5, Math.ceil(Math.max(0, ...classes.map(item => Math.abs(item.winrate - 50)))));
    // Desktop columns: the 50 % line sits where the data puts it, so a lopsided day does not waste half the plot.
    const up = Math.max(2, ...classes.map(item => item.winrate - 50));
    const down = Math.max(2, ...classes.map(item => 50 - item.winrate));
    const perPoint = PLOT_BARS / (up + down);
    return { classes, span, perPoint, lineY: LABEL_ROOM + up * perPoint };
  }, [data.rows]);

  if (model.classes.length === 0) return null;
  const asOf = dataAsOf(data.summary);

  return (
    <section className="arena-view" aria-label="Арена: винрейт классов">
      <header className="arena-view__head">
        <h3>Винрейт классов</h3>
        <span className="arena-view__note">
          Столбец вверх — больше 50 %, вниз — меньше
          {asOf ? (
            <>
              {' · '}Данные на <Time value={asOf} />
            </>
          ) : null}
        </span>
      </header>
      <ol className="arena-cols" style={{ '--line-y': `${model.lineY}px` } as React.CSSProperties}>
        {model.classes.map(item => {
          const delta = item.winrate - 50;
          return (
            <li
              key={`${item.slug ?? item.name}`}
              className="arena-col"
              style={
                {
                  '--dev': Math.min(1, Math.abs(delta) / model.span),
                  '--h': `${Math.abs(delta) * model.perPoint}px`,
                } as React.CSSProperties
              }
            >
              <span className="arena-col__plot">
                <i className="arena-col__bar" data-dir={delta >= 0 ? 'up' : 'down'} />
              </span>
              <b className="arena-col__value" data-dir={delta >= 0 ? 'up' : 'down'}>
                {formatPercent(item.winrate)}
              </b>
              <ClassIcon slug={item.slug} size={44} className="arena-col__icon" />
              <span className="arena-col__name">{item.name}</span>
              <small className="arena-col__pick" title="Выбор класса в драфтах">
                {item.pick === null ? '' : `выбор ${formatPercent(item.pick)}`}
              </small>
            </li>
          );
        })}
      </ol>
      <p className="arena-view__legend">Линия — 50 % побед · выбор класса — как в таблице ниже</p>
    </section>
  );
}
