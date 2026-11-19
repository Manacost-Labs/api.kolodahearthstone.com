'use client';
import { useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowClockwiseIcon } from '@phosphor-icons/react/dist/ssr/ArrowClockwise';

/** «Обновить» of the summary: asks the four modules again (the page itself is rendered on the server). */
export function StatsHomeRefresh() {
  const router = useRouter();
  const [pending, start] = useTransition();
  return (
    <button
      className="button secondary"
      type="button"
      aria-busy={pending}
      onClick={() => start(() => router.refresh())}
    >
      <ArrowClockwiseIcon size={16} aria-hidden="true" /> Обновить
    </button>
  );
}
