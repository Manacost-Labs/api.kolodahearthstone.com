'use client';
import { Pagination } from '../varylo-ui/navigation/Pagination';
import './stats-filters.css';

const labels = {
  previous: 'Назад',
  next: 'Вперёд',
  page: (page: string) => `Страница ${page}`,
  status: (page: string, count: string) => `Страница ${page} из ${count}`,
};

/** «1–50 из 812» and the page buttons; the buttons hide while everything fits on one page. */
export function StatsPager({
  caption,
  page,
  pageCount,
  onPage,
}: {
  caption: string;
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
}) {
  return (
    <div className="stats-pager">
      <span className="stats-pager__caption">{caption}</span>
      {pageCount > 1 && (
        <>
          <Pagination
            className="stats-pager__full"
            size="md"
            pageCount={pageCount}
            value={page}
            onValueChange={onPage}
            labels={labels}
            locale="ru-RU"
            aria-label="Страницы статистики"
          />
          <Pagination
            className="stats-pager__compact"
            size="md"
            variant="compact"
            pageCount={pageCount}
            value={page}
            onValueChange={onPage}
            labels={labels}
            locale="ru-RU"
            aria-label="Страницы статистики"
          />
        </>
      )}
    </div>
  );
}
