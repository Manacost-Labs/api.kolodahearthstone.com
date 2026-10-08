'use client';
import { useTransition } from 'react';
export default function ErrorPage({ retry }: { retry: () => void }) {
  const [pending, startTransition] = useTransition();
  return (
    <section>
      <div className="empty" role="alert">
        <h1>Не удалось загрузить панель</h1>
        <p>Проверьте подключение и повторите попытку.</p>
        <button
          type="button"
          className="button"
          disabled={pending}
          onClick={() => startTransition(() => retry())}
        >
          {pending ? 'Повторяем…' : 'Повторить'}
        </button>
      </div>
    </section>
  );
}
