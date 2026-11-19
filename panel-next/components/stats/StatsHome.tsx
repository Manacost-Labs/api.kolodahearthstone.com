import Link from 'next/link';
import { serverResult } from '@/lib/backend';
import { analyticsRequest } from '@/lib/analytics-query';
import {
  buildCard,
  homeCards,
  type HomeCard,
  type HomeCardDefinition,
  type HomeResult,
} from '@/lib/stats-home';
import type { AnalyticsData, AnalyticsModule } from '@/lib/types';
import { ClassIcon } from '../ClassIcon';
import { Time } from '../ui';
import { StatsHomeRefresh } from './StatsHomeRefresh';
import './stats-home.css';

const failure = 'Не удалось загрузить статистику.';

/** One module on its own: a failure (even a thrown one) stays on its card; a login redirect still goes through. */
async function load(
  definition: HomeCardDefinition,
  modules: Record<string, AnalyticsModule>,
): Promise<HomeResult> {
  const module = modules[definition.module];
  if (!module) return { data: null, message: 'Раздел недоступен в этой версии API.' };
  const { backend } = analyticsRequest(new URLSearchParams(definition.params), definition.module, module);
  try {
    const result = await serverResult<AnalyticsData>('analytics', backend);
    return { data: result.data, message: result.message || failure };
  } catch (error) {
    // Next's redirect/notFound signals are exceptions too; swallowing the login redirect would hide the login.
    const digest = (error as { digest?: unknown } | null)?.digest;
    if (typeof digest === 'string' && digest.startsWith('NEXT_')) throw error;
    return { data: null, message: failure };
  }
}

export async function StatsHome({ modules }: { modules: Record<string, AnalyticsModule> }) {
  const results = await Promise.all(homeCards.map(definition => load(definition, modules)));
  const cards = homeCards.map((definition, index) => buildCard(definition, results[index]));
  return (
    <section className="stats-home">
      <header className="page-head">
        <div>
          <h1>Статистика · Сводка</h1>
          <p>Четыре режима игры: дата данных и три лидера. Нажмите на карточку, чтобы открыть режим.</p>
        </div>
        <StatsHomeRefresh />
      </header>
      <div className="stats-home__grid">
        {cards.map(card => (
          <Card key={card.key} card={card} />
        ))}
      </div>
    </section>
  );
}

function Card({ card }: { card: HomeCard }) {
  return (
    <Link href={card.href} className="stats-home__card" data-card={card.key} data-status={card.status}>
      <header className="stats-home__head">
        <h3>{card.title}</h3>
        {card.asOf ? (
          <span className="stats-home__asof" data-stale={card.stale || undefined}>
            Данные на <Time value={card.asOf} />
            {card.stale ? ' · устарело' : ''}
          </span>
        ) : (
          <span className="stats-home__asof">Дата данных не указана</span>
        )}
      </header>
      {card.status === 'error' ? (
        <p className="stats-home__state stats-home__state--bad" role="status">
          Не удалось загрузить: {card.message}
        </p>
      ) : card.status === 'empty' ? (
        <p className="stats-home__state">Нет данных за выбранный период</p>
      ) : (
        <ol className="stats-home__top">
          {card.rows.map(row => (
            <li key={row.rank} className="stats-home__row" data-icon={row.icon || undefined}>
              <span className="stats-home__rank">{row.rank}</span>
              {row.icon ? <ClassIcon slug={row.classSlug} size={32} className="stats-home__icon" /> : null}
              <span className="stats-home__name">
                <b title={row.title}>{row.title}</b>
                {row.subtitle ? <small lang="en">{row.subtitle}</small> : null}
              </span>
              <span className="stats-home__num stats-home__num--first">{row.first}</span>
              <span className="stats-home__num" data-up={row.up || undefined}>
                {row.second}
              </span>
            </li>
          ))}
        </ol>
      )}
      <footer className="stats-home__foot">
        <span>{card.legend}</span>
        <span className="stats-home__open">
          {card.open} <span aria-hidden="true">→</span>
        </span>
      </footer>
    </Link>
  );
}
