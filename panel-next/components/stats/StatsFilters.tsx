'use client';
import { useId, useRef, type FocusEvent, type KeyboardEvent } from 'react';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { optionLabel, paramLabel } from '@/lib/format';
import { defaultOf, isList, optionHints, optionValues, visibleParams, type Param } from '@/lib/stats-view';
import type { AnalyticsData, AnalyticsModule } from '@/lib/types';
import { Button } from '../varylo-ui/basic/Button';
import { Select } from '../varylo-ui/forms/Select';
import { TextInput } from '../varylo-ui/forms/TextInput';
import './stats-filters.css';

type Changes = Record<string, string | number | null>;
const searchKeys = ['q', 'card_name'];

/** One card of filters: lists apply on choosing, typed fields on Enter or when the field loses focus. */
export function StatsFilters({
  definition,
  data,
  query,
  changed,
  onChange,
  onReset,
}: {
  definition?: AnalyticsModule;
  data?: AnalyticsData;
  query: URLSearchParams;
  changed: boolean;
  onChange: (changes: Changes) => void;
  onReset: () => void;
}) {
  const base = useId();
  // Enter and the blur that follows it must not ask twice for the same value.
  const sent = useRef<Record<string, string>>({});
  const fields = visibleParams(definition);
  if (!fields.length) return null;
  const current = (key: string, param: Param) => query.get(key) ?? defaultOf(param);
  const commit = (key: string, param: Param, raw: string) => {
    let value = raw.trim();
    if (param.type === 'int' && value) {
      const number = Math.floor(Number(value));
      if (!Number.isFinite(number)) return;
      value = String(Math.min(param.max ?? number, Math.max(param.min ?? number, number)));
    }
    if (value === (sent.current[key] ?? current(key, param))) return;
    sent.current[key] = value;
    onChange({ [key]: value || null, page: null });
  };
  const typed = (key: string, param: Param, id: string) => {
    const search = searchKeys.includes(key);
    return (
      <TextInput
        id={id}
        size="lg"
        name={key}
        type={search ? 'search' : 'text'}
        inputMode={param.type === 'int' ? 'numeric' : undefined}
        leftIcon={search ? <MagnifyingGlassIcon size={18} aria-hidden="true" /> : undefined}
        placeholder={
          key === 'q' ? 'Название или часть названия' : key === 'card_name' ? 'Название карты' : undefined
        }
        defaultValue={current(key, param)}
        maxLength={param.max && param.type !== 'int' ? param.max : undefined}
        clearable={search}
        labels={{ clear: 'Очистить' }}
        onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
          // A form with several text fields and no submit button does not submit on Enter by itself.
          if (event.key === 'Enter') commit(key, param, event.currentTarget.value);
        }}
        onBlur={(event: FocusEvent<HTMLInputElement>) => commit(key, param, event.currentTarget.value)}
      />
    );
  };
  return (
    <form className="stats-filters" onSubmit={event => event.preventDefault()}>
      {fields.map(([key, param]) => {
        const id = `${base}-${key}`;
        const list = isList(key, param, data);
        const wide = searchKeys.includes(key);
        return (
          <div className="stats-field" data-wide={wide || undefined} key={key}>
            <label id={`${id}-label`} htmlFor={id}>
              {paramLabel(key)}
            </label>
            {list ? (
              <Select
                id={id}
                size="lg"
                aria-labelledby={`${id}-label`}
                value={current(key, param)}
                onValueChange={value => onChange({ [key]: value, page: null })}
                placeholder="Все"
                options={optionValues(key, param, data, current(key, param)).map(value => ({
                  value,
                  label: optionLabel(key, value),
                  description: optionHints(key, data)[value],
                }))}
              />
            ) : (
              typed(key, param, id)
            )}
            {key === 'q' && <small>Enter — найти</small>}
          </div>
        );
      })}
      <div className="stats-reset" hidden={!changed}>
        <Button type="button" variant="ghost" size="lg" onClick={onReset}>
          Сбросить
        </Button>
      </div>
    </form>
  );
}
