// The panel uses the VaryloUI "vercel" theme; the reader only picks its light or dark mode.
export const themeOptions = [
  { key: 'dark', label: 'Тёмная' },
  { key: 'light', label: 'Светлая' },
  { key: 'system', label: 'Как в системе' },
] as const;
export type ThemePreference = (typeof themeOptions)[number]['key'];
export type ThemeMode = 'light' | 'dark';

const storageKey = 'hsDataTheme-v3';
const defaultPreference: ThemePreference = 'dark';
const darkQuery = '(prefers-color-scheme: dark)';
const keys: readonly string[] = themeOptions.map(option => option.key);

export const isThemePreference = (value: unknown): value is ThemePreference =>
  typeof value === 'string' && keys.includes(value);

// Runs before first paint (see app/layout.tsx), so it must stay dependency-free.
export const themeBootScript =
  `try{var p=localStorage.getItem(${JSON.stringify(storageKey)});` +
  `if(${JSON.stringify(keys)}.indexOf(p)<0)p=${JSON.stringify(defaultPreference)};` +
  `var r=document.documentElement;r.dataset.themePreference=p;r.dataset.theme='vercel';` +
  `r.dataset.mode=p==='system'?(matchMedia(${JSON.stringify(darkQuery)}).matches?'dark':'light'):p}catch(e){}`;

const listeners = new Set<() => void>();

const resolve = (preference: ThemePreference): ThemeMode =>
  preference === 'system' ? (window.matchMedia(darkQuery).matches ? 'dark' : 'light') : preference;

function apply(preference: ThemePreference) {
  const root = document.documentElement;
  root.dataset.themePreference = preference;
  root.dataset.mode = resolve(preference);
}

export function readThemePreference(): ThemePreference {
  const value = document.documentElement.dataset.themePreference;
  return isThemePreference(value) ? value : defaultPreference;
}

export const serverThemePreference = (): ThemePreference => defaultPreference;

/**
 * The mode the page shows now; follows the system while the preference is "system". Derived from the
 * preference, not from data-mode, which ThemeProvider itself writes during hydration.
 */
export const readThemeMode = (): ThemeMode => resolve(readThemePreference());
export const serverThemeMode = (): ThemeMode => 'dark';

export function setThemePreference(preference: ThemePreference) {
  apply(preference);
  try {
    localStorage.setItem(storageKey, preference);
  } catch {}
  for (const listener of listeners) listener();
}

export function subscribeThemePreference(listener: () => void) {
  listeners.add(listener);
  const media = window.matchMedia(darkQuery);
  const followSystem = () => {
    if (readThemePreference() === 'system') {
      apply('system');
      listener();
    }
  };
  const syncTabs = (event: StorageEvent) => {
    if (event.key === storageKey && isThemePreference(event.newValue)) {
      apply(event.newValue);
      listener();
    }
  };
  media.addEventListener('change', followSystem);
  window.addEventListener('storage', syncTabs);
  return () => {
    listeners.delete(listener);
    media.removeEventListener('change', followSystem);
    window.removeEventListener('storage', syncTabs);
  };
}
