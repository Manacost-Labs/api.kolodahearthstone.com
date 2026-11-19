/** Shared by the module views (#109): a cell value as a finite number, or null. */
export const toNumber = (value: unknown): number | null => {
  const raw = typeof value === 'string' ? value.replace('%', '').replace(',', '.').trim() : value;
  const number = typeof raw === 'string' && raw !== '' ? Number(raw) : raw;
  return typeof number === 'number' && Number.isFinite(number) ? number : null;
};

export const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : '');

/** The data date of a module answer: the KPI «Данные на», which the view repeats next to its numbers. */
export const dataAsOf = (summary: { key?: string; value?: unknown }[] | undefined): string | null => {
  const value = summary?.find(item => item.key === 'data_as_of')?.value;
  return typeof value === 'string' && value ? value : null;
};
