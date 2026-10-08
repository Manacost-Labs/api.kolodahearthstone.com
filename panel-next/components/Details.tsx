'use client';
import { useState } from 'react';
import Link from 'next/link';
import { Modal } from './Modal';
import { fieldLabel } from '@/lib/format';
import { imagesFrom, record, text } from '@/lib/model';
import type { Row } from '@/lib/types';
import { Artwork } from './Artwork';
import { Value } from './ui';
const isVideo = (url: string) => /\.(mp4|webm)(\?|$)/i.test(url);

export function RecordTabs({ row, apiBase }: { row: Row; apiBase?: string }) {
  const [tab, setTab] = useState('data');
  const [preview, setPreview] = useState<{ label: string; url: string } | null>(null);
  const images = imagesFrom(row);
  const golden = record(row.golden_variant);
  const ids = [row.card_id || row.hero_card_id || row.skin_id || row.pet_id || row.id, golden.card_id].filter(
    v => v !== null && v !== undefined && v !== '',
  );
  const dbfs = [row.dbf, golden.dbf].filter(v => v !== null && v !== undefined && v !== '');
  return (
    <>
      <div className="tabs" role="tablist" aria-label="Детали записи">
        {(
          [
            ['data', 'Данные'],
            ['images', `Изображения · ${images.length}`],
            ['json', 'JSON API'],
          ] as const
        ).map(([id, name]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={'detail-tab-' + id}
            aria-controls={'detail-panel-' + id}
            aria-selected={tab === id}
            tabIndex={tab === id ? 0 : -1}
            onClick={() => setTab(id)}
            onKeyDown={e => {
              const keys = ['data', 'images', 'json'];
              const i = keys.indexOf(id);
              const delta = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
              if (delta) {
                e.preventDefault();
                const next = keys[(i + delta + 3) % 3];
                setTab(next);
                document.getElementById('detail-tab-' + next)?.focus();
              }
            }}
          >
            {name}
          </button>
        ))}
      </div>
      <section
        role="tabpanel"
        id="detail-panel-data"
        aria-labelledby="detail-tab-data"
        hidden={tab !== 'data'}
      >
        <dl className="facts">
          {Object.entries(row).map(([key, value]) => (
            <div key={key}>
              <dt>{fieldLabel(key)}</dt>
              <dd>
                <Value value={value} name={key} />
              </dd>
            </div>
          ))}
        </dl>
      </section>
      <section
        role="tabpanel"
        id="detail-panel-images"
        aria-labelledby="detail-tab-images"
        hidden={tab !== 'images'}
      >
        {images.length ? (
          <div className="image-grid">
            {images.map(image => (
              <button key={image.url} type="button" onClick={() => setPreview(image)}>
                {isVideo(image.url) ? (
                  <video src={image.url} preload="metadata" muted aria-label={image.label} />
                ) : (
                  <Artwork urls={[image.url]} />
                )}
                <span>{image.label}</span>
              </button>
            ))}
          </div>
        ) : (
          <p className="empty">Изображения отсутствуют</p>
        )}
      </section>
      <section
        role="tabpanel"
        id="detail-panel-json"
        aria-labelledby="detail-tab-json"
        hidden={tab !== 'json'}
      >
        <div className="api-links">
          {(apiBase ? ids : []).map(id => (
            <a
              key={String(id)}
              href={apiBase + '/' + encodeURIComponent(String(id))}
              target="_blank"
              rel="noopener noreferrer"
            >
              <code>
                {apiBase}/{String(id)}
              </code>
            </a>
          ))}
          {(apiBase ? dbfs : []).map(dbf => (
            <a
              key={'dbf-' + String(dbf)}
              href={apiBase + '/by-dbf/' + encodeURIComponent(String(dbf))}
              target="_blank"
              rel="noopener noreferrer"
            >
              <code>
                {apiBase}/by-dbf/{String(dbf)}
              </code>
            </a>
          ))}
        </div>
        <pre>{JSON.stringify(row, null, 2)}</pre>
      </section>
      {preview && (
        <Modal title={preview.label} onClose={() => setPreview(null)}>
          {isVideo(preview.url) ? (
            <video
              className="image-full"
              controls
              muted
              preload="metadata"
              src={preview.url}
              aria-label={preview.label}
            />
          ) : (
            <img className="image-full" src={preview.url} alt={preview.label} />
          )}
        </Modal>
      )}
    </>
  );
}

export function RecordDetails({
  row,
  title,
  onClose,
  editable = false,
  apiBase,
}: {
  row: Row;
  title: string;
  onClose: () => void;
  editable?: boolean;
  apiBase?: string;
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <RecordTabs row={row} apiBase={apiBase} />
      <footer className="modal-actions">
        {editable && (
          <Link
            className="button"
            prefetch={false}
            href={'/?action=edit&id=' + encodeURIComponent(text(row.id))}
          >
            Редактировать
          </Link>
        )}
        <Link
          className="button secondary"
          prefetch={false}
          href={
            '/?action=analytics&stats=card&q=' +
            encodeURIComponent(text(row.name_en || row.name || row.name_ru, ''))
          }
        >
          Статистика
        </Link>
      </footer>
    </Modal>
  );
}
