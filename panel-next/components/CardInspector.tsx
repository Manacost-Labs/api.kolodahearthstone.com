'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowSquareOutIcon } from '@phosphor-icons/react/dist/ssr/ArrowSquareOut';
import { CaretLeftIcon } from '@phosphor-icons/react/dist/ssr/CaretLeft';
import { CaretRightIcon } from '@phosphor-icons/react/dist/ssr/CaretRight';
import { ChartBarIcon } from '@phosphor-icons/react/dist/ssr/ChartBar';
import { PencilSimpleIcon } from '@phosphor-icons/react/dist/ssr/PencilSimple';
import { SparkleIcon } from '@phosphor-icons/react/dist/ssr/Sparkle';
import { cardDetail } from '@/lib/card-detail';
import { text } from '@/lib/model';
import type { Row } from '@/lib/types';
import { Artwork } from './Artwork';
import { CopyButton } from './CopyButton';
import { RecordTabs } from './Details';
import { Modal } from './Modal';
import { StatusBadge, Time } from './ui';

type Props = {
  row: Row;
  cardType: string;
  tribes: Record<string, string>;
  apiBase: string;
  editable: boolean;
  position?: string;
  onPrev?: () => void;
  onNext?: () => void;
  onClose: () => void;
};

export function CardInspector({
  row,
  cardType,
  tribes,
  apiBase,
  editable,
  position,
  onPrev,
  onNext,
  onClose,
}: Props) {
  const detail = cardDetail(row, cardType, tribes);
  const [golden, setGolden] = useState(false);
  const showGolden = golden && detail.images.golden.length > 0;
  const images = showGolden ? detail.images.golden : detail.images.normal;
  const apiId = detail.ids.find(id => id.label === 'Card ID')?.value;

  useEffect(() => {
    const step = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
      // An image preview opened from the tabs sits on top; arrows belong to it, not to the card list.
      if (document.querySelectorAll('dialog[open]').length > 1) return;
      const target = event.target;
      if (target instanceof HTMLElement && target.closest('input,textarea,select,[role="tab"]')) return;
      if (event.key === 'ArrowLeft' && onPrev) onPrev();
      if (event.key === 'ArrowRight' && onNext) onNext();
    };
    window.addEventListener('keydown', step);
    return () => window.removeEventListener('keydown', step);
  }, [onPrev, onNext]);

  return (
    <Modal title={detail.name} onClose={onClose} wide>
      <div className="inspector">
        <div className="inspector-media">
          <div className="inspector-art" data-golden={showGolden || undefined}>
            <Artwork key={images.join('|')} urls={images} alt={detail.name} />
          </div>
          {detail.images.golden.length > 0 && (
            <fieldset className="segmented">
              <legend className="sr-only">Версия карты</legend>
              <button type="button" aria-pressed={!showGolden} onClick={() => setGolden(false)}>
                Обычная
              </button>
              <button type="button" aria-pressed={showGolden} onClick={() => setGolden(true)}>
                <SparkleIcon size={14} weight="fill" aria-hidden="true" /> Золотая
              </button>
            </fieldset>
          )}
        </div>
        <div className="inspector-body">
          {detail.nameEn && detail.nameEn !== detail.name && (
            <p className="inspector-subtitle" lang="en">
              {detail.nameEn}
            </p>
          )}
          <div className="inspector-badges">
            {detail.badges.map(badge => (
              <StatusBadge key={badge.label} label={badge.label} tone={badge.tone ?? 'neutral'} />
            ))}
          </div>
          {detail.stats.length > 0 && (
            <dl className="inspector-stats">
              {detail.stats.map(stat => (
                <div key={stat.key} data-stat={stat.key}>
                  <dt>{stat.label}</dt>
                  <dd>{stat.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {(detail.text.ru || detail.mechanics.length > 0 || detail.text.en) && (
            <section className="inspector-text" aria-label="Текст карты">
              {detail.text.ru && <p>{detail.text.ru}</p>}
              {detail.mechanics.length > 0 && (
                <ul className="mechanics" aria-label="Механики">
                  {detail.mechanics.map(mechanic => (
                    <li key={mechanic}>{mechanic}</li>
                  ))}
                </ul>
              )}
              {detail.text.en && (
                <p className="inspector-text__en" lang="en">
                  {detail.text.en}
                </p>
              )}
              {detail.text.flavor && <p className="inspector-flavor">{detail.text.flavor}</p>}
            </section>
          )}
          {detail.ids.length > 0 && (
            <dl className="inspector-ids">
              {detail.ids.map(id => (
                <div key={id.label}>
                  <dt>{id.label}</dt>
                  <dd>
                    <code>{id.value}</code>
                    <CopyButton value={id.value} label={id.label} />
                  </dd>
                </div>
              ))}
            </dl>
          )}
          <p className="inspector-meta">
            {Boolean(detail.updatedAt) && (
              <span>
                Обновлено <Time value={detail.updatedAt} relative />
              </span>
            )}
            {detail.artist && <span>Художник: {detail.artist}</span>}
            {detail.wikiUrl && (
              <a href={detail.wikiUrl} target="_blank" rel="noopener noreferrer">
                Wiki <ArrowSquareOutIcon size={13} aria-hidden="true" />
              </a>
            )}
          </p>
        </div>
      </div>
      <details className="inspector-more">
        <summary>Изображения, все поля и JSON API</summary>
        <RecordTabs row={row} apiBase={apiBase} />
      </details>
      <footer className="inspector-footer">
        {(onPrev || onNext) && (
          <div className="inspector-nav">
            <button
              type="button"
              className="icon-button"
              onClick={onPrev}
              disabled={!onPrev}
              aria-label="Предыдущая запись"
            >
              <CaretLeftIcon size={18} aria-hidden="true" />
            </button>
            {position && <span>{position}</span>}
            <button
              type="button"
              className="icon-button"
              onClick={onNext}
              disabled={!onNext}
              aria-label="Следующая запись"
            >
              <CaretRightIcon size={18} aria-hidden="true" />
            </button>
          </div>
        )}
        <div className="modal-actions">
          {apiId && (
            <a
              className="button secondary"
              href={`${apiBase}/${encodeURIComponent(apiId)}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ArrowSquareOutIcon size={16} aria-hidden="true" /> API
            </a>
          )}
          <Link
            className="button secondary"
            prefetch={false}
            href={`/?action=analytics&stats=card&q=${encodeURIComponent(text(row.name_en || row.name || row.name_ru, ''))}`}
          >
            <ChartBarIcon size={16} aria-hidden="true" /> Статистика
          </Link>
          {editable && (
            <Link
              className="button"
              prefetch={false}
              href={`/?action=edit&id=${encodeURIComponent(text(row.id))}`}
            >
              <PencilSimpleIcon size={16} aria-hidden="true" /> Редактировать
            </Link>
          )}
        </div>
      </footer>
    </Modal>
  );
}
