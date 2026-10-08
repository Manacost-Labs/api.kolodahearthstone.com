'use client';
import { useEffect, useRef, useState } from 'react';
import { ImageBrokenIcon } from '@phosphor-icons/react/dist/ssr/ImageBroken';

/** Tries each URL in turn; shimmers while loading and fades the image in once it is ready. */
export function Artwork({ urls, alt = '' }: { urls: string[]; alt?: string }) {
  const [index, setIndex] = useState(0);
  const [ready, setReady] = useState('');
  const image = useRef<HTMLImageElement>(null);
  const url = urls[index];
  // A cached image can finish before hydration, and a cached broken one never fires onError.
  // biome-ignore lint/correctness/useExhaustiveDependencies: index selects the image being re-checked
  useEffect(() => {
    const img = image.current;
    if (!img?.complete) return;
    if (img.naturalWidth) setReady(img.getAttribute('src') || '');
    else setIndex(value => value + 1);
  }, [index]);
  if (!url)
    return (
      <span className="artwork" data-state="missing">
        <span className="art-empty">
          <ImageBrokenIcon size={28} aria-hidden="true" />
          Нет изображения
        </span>
      </span>
    );
  return (
    <span className="artwork" data-state={ready === url ? 'ready' : 'loading'}>
      <img
        key={url}
        ref={image}
        src={url}
        alt={alt}
        loading="lazy"
        decoding="async"
        onLoad={() => setReady(url)}
        onError={() => setIndex(value => value + 1)}
      />
    </span>
  );
}
