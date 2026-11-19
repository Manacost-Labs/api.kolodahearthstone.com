'use client';
import { useCallback, useState } from 'react';
import './thumb.css';

/** One or two capital letters of the label: the placeholder under a picture that has not arrived yet. */
const monogram = (alt: string) => {
  const words = alt
    .trim()
    .split(/[\s\-–—_:]+/u)
    .filter(Boolean);
  const letters = words.length > 1 ? [words[0], words[1]] : [...(words[0] ?? '')].slice(0, 1);
  return (
    letters
      .map(part => [...part][0])
      .join('')
      .toLocaleUpperCase('ru-RU') || '·'
  );
};

// A thumbnail shows a monogram until the picture loads (the cold S3 mount can take a second) and falls back
// to a plain link when it is broken, never an empty box.
export function Thumb({ url, alt }: { url: string; alt: string }) {
  const [state, setState] = useState<'loading' | 'ready' | 'broken'>('loading');
  // A cached picture can finish before React attaches onLoad: read it at mount.
  const ref = useCallback((node: HTMLImageElement | null) => {
    if (node?.complete) setState(node.naturalWidth > 0 ? 'ready' : 'broken');
  }, []);
  const broken = state === 'broken';
  return (
    <a
      className={broken ? 'value-link' : 'value-thumb'}
      data-state={state}
      href={url}
      target="_blank"
      rel="noopener noreferrer"
    >
      {broken ? (
        `${alt}: недоступно`
      ) : (
        <>
          <span className="value-thumb__ph" aria-hidden="true">
            {monogram(alt)}
          </span>
          <img
            ref={ref}
            src={url}
            alt={alt}
            loading="lazy"
            decoding="async"
            onLoad={() => setState('ready')}
            onError={() => setState('broken')}
          />
        </>
      )}
    </a>
  );
}
