'use client';
import { useState } from 'react';
import { countLabel, scopeLabels } from '@/lib/format';
import { text, tokenState, tokenUsage } from '@/lib/model';
import { mutate, MutationError } from '@/lib/client';
import type { PageData, Row } from '@/lib/types';
import { Modal } from './Modal';
import { StatusBadge, Time } from './ui';
export function Tokens({ data }: { data: PageData }) {
  const [issued, setIssued] = useState<Row | null>(null);
  const [nonce, setNonce] = useState(data.issueNonce || '');
  const [tokens, setTokens] = useState(data.tokens || []);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [revoke, setRevoke] = useState<Row | null>(null);
  // Live tokens first, then the expired and revoked ones; the order within a group is the server's.
  const rank = { expiring: 0, active: 0, expired: 1, revoked: 2 };
  const sorted = tokens
    .map(token => ({ token, state: tokenState(token) }))
    .sort((a, b) => rank[a.state.key] - rank[b.state.key]);
  const submit = async (form: FormData) => {
    setBusy(true);
    setError('');
    try {
      const result = await mutate(form);
      setIssued(result.issuedToken || null);
      setNonce(result.issueNonce);
      setTokens(result.tokens || []);
      setMessage(result.message);
      setRevoke(null);
    } catch (e) {
      if (e instanceof MutationError && typeof e.payload.issueNonce === 'string')
        setNonce(e.payload.issueNonce);
      setError(e instanceof Error ? e.message : 'Не удалось выполнить действие.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <section>
      <header className="page-head">
        <div>
          <h1>API-токены</h1>
          <p>Отдельные права и сроки действия для каждого сервиса.</p>
        </div>
        <StatusBadge
          tone={data.tokenConfigured ? 'good' : 'bad'}
          label={data.tokenConfigured ? 'Подключено' : 'Не настроено'}
        />
      </header>
      {(error || data.tokenError) && (
        <p className="notice bad" role="alert">
          {error || data.tokenError}
        </p>
      )}
      {message && (
        <p className="notice" role="status">
          {message}
        </p>
      )}
      {issued && (
        <section className="secret-card" role="status">
          <span className="eyebrow">Показывается один раз</span>
          <h2>Скопируйте новый токен</h2>
          <p>После ухода со страницы секрет восстановить нельзя.</p>
          <code>{text(issued.token)}</code>
          <button
            type="button"
            className="button"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(text(issued.token));
                setMessage('Токен скопирован.');
              } catch {
                setError('Выделите и скопируйте токен вручную.');
              }
            }}
          >
            Скопировать
          </button>
        </section>
      )}
      <div className="token-layout">
        <form
          className="panel-card"
          onSubmit={e => {
            e.preventDefault();
            void submit(new FormData(e.currentTarget));
          }}
        >
          <h2>Выпустить токен</h2>
          <input type="hidden" name="action" value="issue_api_token" />
          <input type="hidden" name="csrf" value={data.csrf} />
          <input type="hidden" name="form_nonce" value={nonce} />
          <label>
            <span>Название сервиса</span>
            <input name="name" placeholder="Например, сайт с колодами" required maxLength={120} />
          </label>
          <label>
            <span>Срок действия, дней</span>
            <input name="expires_in_days" type="number" min={1} max={365} defaultValue={90} required />
          </label>
          <fieldset>
            <legend>Права доступа</legend>
            {Object.entries(data.scopeCatalog || {}).map(([key, name]) => (
              <label className="scope-option" key={key}>
                <input type="checkbox" name="scopes[]" value={key} defaultChecked={key === 'database:read'} />
                <span>
                  <strong>{name.label}</strong>
                  <small>{name.description}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <button type="submit" className="button" disabled={busy || !data.tokenConfigured}>
            {busy ? 'Выпускаем…' : 'Выпустить'}
          </button>
        </form>
        {/* biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable regions must be reachable by keyboard */}
        <div className="table-scroll" role="region" tabIndex={0} aria-label="Выпущенные токены">
          <table className="tokens-table">
            <thead>
              <tr>
                <th scope="col">Название</th>
                <th scope="col">Права</th>
                <th scope="col">Действует до</th>
                <th scope="col">Использование</th>
                <th scope="col">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(({ token, state }) => (
                <tr key={text(token.id)} data-state={state.key}>
                  <th scope="row">
                    <strong>{text(token.name)}</strong>
                    <code>{text(token.id)}</code>
                    {state.key === 'revoked' && <StatusBadge tone="neutral" label="Отозван" />}
                    {state.key === 'expired' && <StatusBadge tone="bad" label="Срок истёк" />}
                    {state.key === 'expiring' && (
                      <StatusBadge
                        tone="warning"
                        label={`Истекает через ${countLabel(state.days, { one: 'день', few: 'дня', many: 'дней' })}`}
                      />
                    )}
                  </th>
                  <td data-label="Права">
                    {(Array.isArray(token.scopes) ? token.scopes.map(String) : [text(token.scopes)])
                      .map(scope => data.scopeCatalog?.[scope]?.label ?? scopeLabels[scope] ?? scope)
                      .join(', ')}
                  </td>
                  <td data-label="Действует до">
                    <Time value={token.expires_at} />
                  </td>
                  <td data-label="Использование">
                    {(() => {
                      const usage = tokenUsage(token);
                      return (
                        <>
                          <strong>
                            {countLabel(usage.requests, { one: 'запрос', few: 'запроса', many: 'запросов' })}
                          </strong>
                          <small>
                            {countLabel(usage.errors, { one: 'ошибка', few: 'ошибки', many: 'ошибок' })}
                            {usage.month && ' · ' + usage.month}
                          </small>
                        </>
                      );
                    })()}
                  </td>
                  <td>
                    {!Boolean(token.revoked_at) && text(token.id) !== data.managerId && (
                      <button
                        type="button"
                        className="link-button revoke-link"
                        aria-label={`Отозвать токен «${text(token.name)}»`}
                        onClick={() => setRevoke(token)}
                      >
                        Отозвать
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!tokens.length && <p className="empty">Токены отсутствуют.</p>}
        </div>
      </div>
      {revoke && (
        <Modal title="Отозвать токен?" onClose={() => setRevoke(null)}>
          <p>Сервис «{text(revoke.name)}» потеряет доступ к API.</p>
          <div className="form-actions">
            <button type="button" className="button secondary" onClick={() => setRevoke(null)}>
              Отмена
            </button>
            <button
              type="button"
              className="button danger"
              disabled={busy}
              onClick={() => {
                const form = new FormData();
                form.set('action', 'revoke_api_token');
                form.set('csrf', data.csrf);
                form.set('token_id', text(revoke.id));
                void submit(form);
              }}
            >
              Отозвать
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
