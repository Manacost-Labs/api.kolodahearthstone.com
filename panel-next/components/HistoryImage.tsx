'use client';
import { useCallback, useState } from 'react';
import { archivedHistoryImage, historyDate, type HistoryVersion } from '@/lib/card-history';

// The two comparison images are what the reader came for: high priority, async decode and a
// skeleton of the final size until the bytes arrive.
export function HistoryImage({ version, label }: { version: HistoryVersion; label: string }) {
  const image = archivedHistoryImage(version);
  const [state, setState] = useState<{ url: string; status: 'loaded' | 'failed' } | null>(null);
  const status = state?.url === image?.url ? state?.status : undefined;
  // A cached image can finish before React attaches onLoad.
  const ref = useCallback((node: HTMLImageElement | null) => {
    if (node?.complete && node.naturalWidth > 0)
      setState(current => current ?? { url: node.currentSrc, status: 'loaded' });
  }, []);
  return (
    <figure className="history-image" data-state={image ? (status ?? 'loading') : 'empty'}>
      {image && status !== 'failed' ? (
        <div className="history-image__frame">
          {/* Content-addressed snapshots retain their bytes and aspect ratio. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            ref={ref}
            src={image.url}
            alt={`${label}: сохранённое изображение карты`}
            width={256}
            height={384}
            decoding="async"
            fetchPriority="high"
            onLoad={() => setState({ url: image.url, status: 'loaded' })}
            onError={() => setState({ url: image.url, status: 'failed' })}
          />
        </div>
      ) : (
        <p className="muted">
          {image ? 'Не удалось загрузить изображение.' : 'Изображение этой версии ещё не подтверждено.'}
        </p>
      )}
      <figcaption>
        <strong>{label}</strong>
        {image && <span>Сохранено {historyDate(image.captured_at)}</span>}
      </figcaption>
    </figure>
  );
}
