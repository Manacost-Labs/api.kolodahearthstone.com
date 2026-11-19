'use client';
import type { AnalyticsModule } from '@/lib/types';
import { groupTabs, otherGroup, placeOf } from '@/lib/stats-sections';
import { Tabs } from './varylo-ui/navigation/Tabs';

/**
 * Tabs of the statistics modules that share the current group. A group of one module, and the
 * monitoring pages, have nothing to switch between, so nothing is drawn.
 */
export function StatsModuleTabs({
  modules,
  module,
  onSelect,
}: {
  modules: Record<string, AnalyticsModule>;
  module: string;
  onSelect: (key: string) => void;
}) {
  const place = placeOf(module, modules);
  if (place.section !== 'statistics') return null;
  const group = place.group.key === 'other' ? (otherGroup(modules) ?? place.group) : place.group;
  const tabs = groupTabs(group, modules);
  if (tabs.length < 2) return null;
  return (
    <Tabs
      className="stats-tabs"
      variant="pills"
      label={`Наборы статистики: ${group.label}`}
      value={module}
      onValueChange={onSelect}
      items={tabs.map(tab => ({ value: tab.key, label: tab.label }))}
    />
  );
}
