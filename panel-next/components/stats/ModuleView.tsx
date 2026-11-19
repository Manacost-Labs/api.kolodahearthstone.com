import type { ComponentType } from 'react';
import type { AnalyticsData } from '@/lib/types';
import { ArenaView } from './ArenaView';
import { BgHeroesView, BgMinionsView } from './BattlegroundsView';
import { MetaView } from './MetaView';

export type ModuleViewProps = { module: string; data: AnalyticsData };

/**
 * The slot for a module's own view between the KPI cards and the table (charts, #107 and #109).
 * A module without an entry here shows nothing, so `Analytics.tsx` stays untouched when a view is added.
 */
const views: Record<string, ComponentType<ModuleViewProps>> = {
  meta: MetaView,
  hsguru_archetypes: MetaView,
  bg_heroes: BgHeroesView,
  bg_minions: BgMinionsView,
  arena: ArenaView,
};

export function ModuleView({ module, data }: ModuleViewProps) {
  const View = views[module];
  return View ? <View module={module} data={data} /> : null;
}
