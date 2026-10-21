'use client';
import { useCallback, useState } from 'react';
import { useSearchParams } from 'next/navigation';

/**
 * Client-side filter state mirrored into the address bar, so a filtered view can be
 * reloaded or shared. replaceState keeps Back for real navigation and skips the server.
 */
export function useUrlState(name: string, fallback = ''): [string, (value: string) => void] {
  const params = useSearchParams();
  const [value, setValue] = useState(() => params.get(name) ?? fallback);
  const update = useCallback(
    (next: string) => {
      setValue(next);
      const url = new URLSearchParams(window.location.search);
      if (!next || next === fallback) url.delete(name);
      else url.set(name, next);
      window.history.replaceState(null, '', window.location.pathname + (url.size ? `?${url}` : ''));
    },
    [name, fallback],
  );
  return [value, update];
}
