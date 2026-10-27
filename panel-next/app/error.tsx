'use client';
import { useTransition } from 'react';
import Link from 'next/link';
export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  const [pending, startTransition] = useTransition();
  // Server errors arrive without details; the digest matches the server log line.
  const digest = error instanceof Error && 'digest' in error ? String(error.digest || '') : '';
  return (
    <section>
      <div className="empty" role="alert">
        <h1>Не удалось загрузить панель</h1>
        <p>Сервер данных панели не ответил. Повторите попытку через несколько секунд.</p>
        {digest && (
          <p>
            Код для логов: <code>{digest}</code>
          </p>
        )}
        <div className="form-actions">
          <button
            type="button"
            className="button"
            disabled={pending}
            onClick={() => startTransition(() => retry())}
          >
            {pending ? 'Повторяем…' : 'Повторить'}
          </button>
          <Link className="button secondary" href="/" prefetch={false}>
            В каталог
          </Link>
        </div>
      </div>
    </section>
  );
}
