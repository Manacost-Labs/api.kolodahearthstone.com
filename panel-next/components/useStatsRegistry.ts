'use client';
import { useEffect, useState } from 'react';
import { apiPath } from '@/lib/client';

export type StatsRegistry = Record<string, { title: string }>;

// Statistics modules come from the analytics registry; one request per page load serves the menu and Ctrl+K.
let registryRequest: Promise<StatsRegistry> | null = null;
function loadStatsRegistry(): Promise<StatsRegistry> {
  registryRequest ??= fetch(`${apiPath}?action=analytics_registry`, { cache: 'no-store' })
    .then(response => response.json())
    .then(payload =>
      Object.fromEntries(
        Object.entries((payload?.modules ?? {}) as Record<string, { title?: string }>).map(
          ([key, module]) => [key, { title: module.title || key }],
        ),
      ),
    )
    .catch(() => {
      registryRequest = null;
      return {};
    });
  return registryRequest;
}

/** Registry modules (key → title); empty until loaded, and when the request fails. */
export function useStatsRegistry(): StatsRegistry {
  const [registry, setRegistry] = useState<StatsRegistry>({});
  useEffect(() => {
    let live = true;
    void loadStatsRegistry().then(list => {
      if (live) setRegistry(list);
    });
    return () => {
      live = false;
    };
  }, []);
  return registry;
}
