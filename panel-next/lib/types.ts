export type Row = Record<string, unknown>;
export interface User {
  id: number;
  login: string;
}
export interface PageData {
  ok: boolean;
  version: number;
  action: string;
  title: string;
  user: User;
  csrf: string;
  logoutCsrf: string;
  parserCsrf: string;
  message: string;
  error: string;
  cardType: string;
  records: Row[];
  form: Row;
  page: number;
  perPage: number;
  totalPages: number;
  total: number;
  from: number;
  to: number;
  categories: Record<string, string>;
  tribes: Record<string, string>;
  mediaLabels: Record<string, string>;
  rarities: Record<string, string>;
  activeFilters: { label: string; href: string }[];
  tokens?: Row[];
  managerId?: string;
  tokenConfigured?: boolean;
  tokenError?: string;
  issueNonce?: string;
  scopeCatalog?: Record<string, { label: string; description: string }>;
  issuedToken?: Row | null;
  terms?: Record<string, Row[]>;
  termLabels?: Record<string, string>;
}
export interface AnalyticsModule {
  title: string;
  description: string;
  params: Record<
    string,
    {
      type: string;
      values?: string[];
      default?: string | number;
      min?: number;
      max?: number;
      required?: boolean;
    }
  >;
}
export interface AnalyticsData {
  ok: boolean;
  title: string;
  description: string;
  columns: { key: string; label: string; type?: string }[];
  rows: Row[];
  summary: { key?: string; label: string; value: string | number; type?: string; tone?: string }[];
  /** Values of the list filters (#104); the period list comes from the data, so it is never in the registry. */
  options?: Record<string, string[]>;
  meta: Row;
  [key: string]: unknown;
}
