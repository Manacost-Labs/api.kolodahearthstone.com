'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { PanelLink, useWorkspaceNavigation } from './WorkspaceNavigation';
import { mutate } from '@/lib/client';
import { ArrowLeftIcon } from '@phosphor-icons/react/dist/ssr/ArrowLeft';
import { text } from '@/lib/model';
import type { PageData } from '@/lib/types';
import { Modal } from './Modal';
export function Editor({ data }: { data: PageData }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState(false);
  const router = useRouter();
  const { catalogUrl } = useWorkspaceNavigation();
  const submit = async (form: FormData) => {
    setBusy(true);
    setError('');
    try {
      await mutate(form);
      router.push('/');
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось сохранить карту.');
    } finally {
      setBusy(false);
    }
  };
  const field = (key: string, name: string, numeric = false) => {
    const required = key === 'name' || key === 'card_id';
    return (
      <label key={key}>
        <span>
          {name}
          {required && (
            <abbr className="required-mark" title="Обязательное поле">
              *
            </abbr>
          )}
        </span>
        <input
          name={key}
          defaultValue={text(data.form[key], '')}
          type={numeric ? 'number' : 'text'}
          required={required}
          min={numeric ? 0 : undefined}
          max={key === 'tavern_tier' ? 7 : undefined}
        />
      </label>
    );
  };
  const editing = Boolean(data.form.id);
  return (
    <section>
      <header className="page-head">
        <div>
          <h1>{data.title}</h1>
          <p>
            {editing
              ? `«${text(data.form.name)}» · ${text(data.form.card_id)}`
              : 'Обязательны только название RU и Card ID, остальное можно дополнить позже.'}
          </p>
        </div>
        <PanelLink href={catalogUrl} className="button secondary" prefetch={false} restoreCatalog>
          <ArrowLeftIcon size={16} aria-hidden="true" /> К каталогу
        </PanelLink>
      </header>
      {error && (
        <p className="notice bad" role="alert">
          {error}
        </p>
      )}
      <form
        className="editor-form"
        onSubmit={e => {
          e.preventDefault();
          void submit(new FormData(e.currentTarget));
        }}
      >
        <input type="hidden" name="action" value="save" />
        <input type="hidden" name="csrf" value={data.csrf} />
        <input type="hidden" name="id" value={text(data.form.id, '')} />
        <fieldset className="form-section">
          <legend>Карта</legend>
          <div className="form-grid">
            {field('name', 'Название RU')}
            {field('name_en', 'Название EN')}
            {field('card_id', 'Card ID')}
            {field('dbf', 'DBF', true)}
            <label>
              <span>Категория</span>
              <select name="card_type" defaultValue={text(data.form.card_type, 'minion')}>
                <option value="minion">Существо</option>
                <option value="spell">Заклинание</option>
              </select>
            </label>
            <label>
              <span>Тип существа</span>
              <select name="creature_type" defaultValue={text(data.form.creature_type, '')}>
                <option value="">Не указан</option>
                {Object.entries(data.tribes).map(([key, name]) => (
                  <option key={key} value={key}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </fieldset>
        <fieldset className="form-section">
          <legend>Характеристики и пул</legend>
          <div className="form-grid">
            {field('tavern_tier', 'Уровень таверны', true)}
            {field('attack', 'Атака', true)}
            {field('health', 'Здоровье', true)}
            <label className="check-label">
              <input type="checkbox" name="in_pool" defaultChecked={Number(data.form.in_pool) === 1} /> В пуле
              таверны
            </label>
            <label className="check-label">
              <input type="checkbox" name="duos_only" defaultChecked={Number(data.form.duos_only) === 1} />{' '}
              Только дуо
            </label>
          </div>
        </fieldset>
        <label className="form-section">
          <span>Описание и механики</span>
          <textarea name="notes" rows={5} defaultValue={text(data.form.notes, '')} />
        </label>
        <details className="extra-data">
          <summary>Заменить изображения</summary>
          <div className="form-grid">
            {[
              ['card_image_file', 'Обычная карта'],
              ['golden_image_file', 'Золотая карта'],
              ['art_image_file', 'Арт'],
              ['framed_image_file', 'Арт в рамке'],
            ].map(([key, name]) => (
              <label key={key}>
                <span>{name}</span>
                <input type="file" name={key} accept="image/png,image/jpeg,image/webp" />
              </label>
            ))}
          </div>
        </details>
        {/* Save stays in reach on long forms; the destructive action sits apart from it. */}
        <div className="form-actions editor-actions">
          <button className="button" disabled={busy} type="submit">
            {busy ? 'Сохраняем…' : editing ? 'Сохранить изменения' : 'Создать карту'}
          </button>
          <PanelLink href={catalogUrl} className="button secondary" prefetch={false} restoreCatalog>
            Отмена
          </PanelLink>
          {editing && (
            <button
              className="button danger-outline"
              type="button"
              disabled={busy}
              onClick={() => setConfirm(true)}
            >
              Удалить карту
            </button>
          )}
        </div>
      </form>
      {confirm && (
        <Modal title="Удалить карту?" onClose={() => setConfirm(false)}>
          <p>Карта «{text(data.form.name)}» будет удалена из базы.</p>
          <div className="form-actions">
            <button type="button" className="button secondary" onClick={() => setConfirm(false)}>
              Отмена
            </button>
            <button
              type="button"
              className="button danger"
              disabled={busy}
              onClick={() => {
                const form = new FormData();
                form.set('action', 'delete');
                form.set('id', text(data.form.id));
                form.set('csrf', data.csrf);
                void submit(form);
              }}
            >
              Удалить
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
