'use client';
import {
  cardLangs,
  fallbackNote,
  langNames,
  missingNote,
  parseLang,
  type CardLang,
  type RowLocale,
} from '@/lib/card-locale';
import { SegmentedControl } from './varylo-ui/navigation/SegmentedControl';
import './card-locale.css';

/** The card language: RU · EN · PL · ES. The group and its options have full accessible names. */
export function LocaleSwitch({
  lang,
  onChange,
  className,
}: {
  lang: CardLang;
  onChange: (lang: CardLang) => void;
  className?: string;
}) {
  return (
    <SegmentedControl
      className={className}
      aria-label="Язык карт"
      size="sm"
      value={lang}
      onValueChange={value => {
        const next = parseLang(value);
        if (next) onChange(next);
      }}
      options={cardLangs.map(code => ({
        value: code,
        label: <span aria-hidden="true">{langNames[code].short}</span>,
        ariaLabel: langNames[code].full,
      }))}
    />
  );
}

/** A small mark after a name whose language differs from the chosen one: no translation, or no catalogue entry. */
export function LocaleMark({ lang, entry }: { lang: CardLang; entry: RowLocale }) {
  const note =
    entry.state === 'ok' && entry.value.fallback
      ? { short: 'EN', text: fallbackNote(lang) }
      : entry.state === 'missing'
        ? { short: 'RU', text: missingNote }
        : null;
  if (!note) return null;
  return (
    <em className="locale-note" title={note.text} lang="ru">
      <span aria-hidden="true">{note.short}</span>
      <span className="sr-only">{note.text}</span>
    </em>
  );
}
