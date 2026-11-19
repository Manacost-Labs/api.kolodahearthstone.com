import { getPage, getSession, serverData, serverResult } from '@/lib/backend';
import { analyticsRequest, missingHint } from '@/lib/analytics-query';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { matchesCard } from '@/lib/card-detail';
import { catalogView, defaultPageSize } from '@/lib/catalog-state';
import { Catalog } from '@/components/Catalog';
import { Analytics } from '@/components/Analytics';
import { Parsers } from '@/components/Parsers';
import { Tokens } from '@/components/Tokens';
import { Editor } from '@/components/Editor';
import { Terms } from '@/components/Terms';
import { resolveModule } from '@/lib/stats-sections';
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
  if (action === 'list' && !query.has('per_page'))
    query.set('per_page', String(defaultPageSize(catalogView(query.get('view')))));
  if (action === 'analytics') {
    const registry = await serverData<{ modules: Record<string, AnalyticsModule> }>(
      'panel',
      new URLSearchParams({ action: 'analytics_registry' }),
    );
    // Without `stats` (or with a key the registry does not know) the page opens «Мета», not the registry.
    const moduleKey = resolveModule(query.get('stats'), registry.modules);
    const definition = registry.modules[moduleKey];
    const { backend, page, missing } = analyticsRequest(query, moduleKey, definition);
    let data: AnalyticsData = {
      ok: true,
      title: definition.title,
      description: definition.description,
      columns: [],
      rows: [],
      summary: [],
      meta: {},
    };
    let notice: { tone: 'hint' | 'error'; text: string } | null = null;
    if (missing.length) notice = { tone: 'hint', text: missingHint(missing) };
    else {
      // A failing module is shown inside its page: the menu stays and "Обновить" asks again.
      const result = await serverResult<AnalyticsData>('analytics', backend);
      if (result.data) data = result.data;
      else notice = { tone: 'error', text: result.message };
    }
    return (
      <>
        {notice && (
          <p
            role={notice.tone === 'error' ? 'alert' : 'status'}
            className={notice.tone === 'error' ? 'notice bad' : 'notice'}
          >
            {notice.text}
          </p>
        )}
        <Analytics
          key={page.toString()}
          data={data}
          modules={registry.modules}
          module={moduleKey}
          query={page.toString()}
        />
      </>
    );
  }
  if (action === 'parsers') {
    const session = await getSession();
    const response = await serverData<{ data: Row }>('parsers', new URLSearchParams());
    // Keyed by the filters so a link to another filtered view (e.g. from Ctrl+K) starts from that URL.
    return (
      <Parsers
        key={['q', 'status', 'section'].map(key => query.get(key) || '').join('|')}
        initial={response.data}
        csrf={session.parserCsrf}
      />
    );
  }
  const allowed = ['list', 'edit', 'new', 'api_tokens', 'wiki_terms'];
  if (!allowed.includes(action)) query.set('action', 'list');
  // ?card= is client state for the inspector; PHP does not need it.
  const cardParam = query.get('card') || '';
  query.delete('card');
  const data = await getPage(query);
  let focus: Row | null = null;
  if (cardParam && data.action === 'list' && !data.records.some(row => matchesCard(row, cardParam))) {
    // PHP search is a LIKE match, so look the card up and keep only the exact identifier.
    const lookup = await getPage(
      new URLSearchParams({ action: 'list', card_type: data.cardType || '', q: cardParam, per_page: '150' }),
    );
    focus = lookup.records.find(row => matchesCard(row, cardParam)) ?? null;
  }
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
        <Catalog data={data} query={query.toString()} focus={focus} />
      )}
    </>
  );
}
