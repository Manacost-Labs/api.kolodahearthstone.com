import { getPage, getSession, serverData } from '@/lib/backend';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { catalogView } from '@/lib/catalog-state';
import { Catalog } from '@/components/Catalog';
import { Analytics } from '@/components/Analytics';
import { Parsers } from '@/components/Parsers';
import { Tokens } from '@/components/Tokens';
import { Editor } from '@/components/Editor';
import { Terms } from '@/components/Terms';
import type { AnalyticsData, AnalyticsModule, Row } from '@/lib/types';
export const dynamic = 'force-dynamic';
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const input = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) if (typeof value === 'string') query.set(key, value);
  const action = query.get('action') || 'list';
  if (action === 'list' && !query.has('view')) {
    const saved = (await cookies()).get('hs_catalog_view')?.value;
    if (saved && ['grid', 'list', 'tiles'].includes(saved)) {
      await getSession();
      query.set('view', catalogView(saved));
      if (saved === 'tiles') query.set('per_page', '15');
      redirect((process.env.NEXT_PUBLIC_PANEL_BASE_PATH || '') + '/?' + query.toString());
    }
  }
  if (action === 'list' && query.get('view') === 'tiles') query.set('per_page', '15');
  if (action === 'analytics') {
    const registry = await serverData<{ modules: Record<string, AnalyticsModule> }>(
      'panel',
      new URLSearchParams({ action: 'analytics_registry' }),
    );
    const candidate = query.get('stats') || 'overview';
    const moduleKey = registry.modules[candidate] ? candidate : 'overview';
    const statsQuery = new URLSearchParams(query);
    statsQuery.delete('action');
    statsQuery.delete('stats');
    statsQuery.set('module', moduleKey);
    const statsSearch = statsQuery.get('stats_q');
    if (statsSearch !== null) {
      statsQuery.set('q', statsSearch);
      statsQuery.delete('stats_q');
    }
    const data = await serverData<AnalyticsData>('analytics', statsQuery);
    return (
      <Analytics
        key={query.toString()}
        data={data}
        modules={registry.modules}
        module={moduleKey}
        query={query.toString()}
      />
    );
  }
  if (action === 'parsers') {
    const session = await getSession();
    const response = await serverData<{ data: Row }>('parsers', new URLSearchParams());
    return <Parsers initial={response.data} csrf={session.parserCsrf} />;
  }
  const allowed = ['list', 'edit', 'new', 'api_tokens', 'wiki_terms'];
  if (!allowed.includes(action)) query.set('action', 'list');
  const data = await getPage(query);
  return (
    <>
      {data.error && (
        <p role="alert" className="notice bad">
          {data.error}
        </p>
      )}
      {data.message && (
        <p role="status" className="notice">
          {data.message}
        </p>
      )}
      {data.action === 'api_tokens' ? (
        <Tokens data={data} />
      ) : ['edit', 'new'].includes(data.action) ? (
        <Editor data={data} />
      ) : data.action === 'wiki_terms' ? (
        <Terms data={data} />
      ) : (
        <Catalog data={data} query={query.toString()} />
      )}
    </>
  );
}
