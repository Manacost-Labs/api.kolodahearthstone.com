'use client';
import { useSearchParams } from 'next/navigation';
import { catalogView } from '@/lib/catalog-state';
export default function Loading() {
  const params = useSearchParams();
  const action = params.get('action') || 'list';
  const view = catalogView(params.get('view'));
  const catalog = action === 'list';
  const count = catalog ? (view === 'tiles' ? 15 : Math.min(Number(params.get('per_page')) || 8, 150)) : 4;
  return (
    <section className="page-loading" role="status" aria-label="Загрузка данных" aria-busy="true">
      <div className="loading-heading" />
      <div className="loading-caption" />
      {catalog && <div className="loading-filters" />}
      <div className={catalog && view === 'tiles' ? 'deck-grid-viewport' : undefined}>
        <div
          className={
            catalog
              ? view === 'tiles'
                ? 'loading-tiles'
                : view === 'list'
                  ? 'loading-table'
                  : 'skeleton-grid'
              : 'loading-section'
          }
          aria-hidden="true"
        >
          {Array.from({ length: count }, (_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static placeholders never reorder
            <div className="skeleton" key={i} />
          ))}
        </div>
      </div>
      <span className="sr-only">Загрузка данных…</span>
    </section>
  );
}
