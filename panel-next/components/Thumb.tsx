'use client';
import { useState } from 'react';

// A broken thumbnail falls back to a plain link instead of an empty box.
export function Thumb({ url, alt }: { url: string; alt: string }) {
  const [broken, setBroken] = useState(false);
  return (
    <a className={broken ? 'value-link' : 'value-thumb'} href={url} target="_blank" rel="noopener noreferrer">
      {broken ? (
        `${alt}: недоступно`
      ) : (
        <img src={url} alt={alt} loading="lazy" decoding="async" onError={() => setBroken(true)} />
      )}
    </a>
  );
}
