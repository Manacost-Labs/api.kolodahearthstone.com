export const themeOptions = [
  { key: 'system', label: 'Как в системе' },
  { key: 'light', label: 'Светлая' },
  { key: 'dark', label: 'Тёмная' },
  { key: 'tavern', label: 'Таверна' },
  { key: 'arcane', label: 'Аркана' },
] as const;
export type ThemePreference = (typeof themeOptions)[number]['key'];

const storageKey = 'hsDataTheme-v2';
const defaultPreference: ThemePreference = 'light';
const darkQuery = '(prefers-color-scheme: dark)';
const keys: readonly string[] = themeOptions.map(option => option.key);

export const isThemePreference = (value: unknown): value is ThemePreference =>
  typeof value === 'string' && keys.includes(value);

// Runs before first paint (see app/layout.tsx), so it must stay dependency-free.
export const themeBootScript =
  `try{var p=localStorage.getItem(${JSON.stringify(storageKey)});` +
  `if(${JSON.stringify(keys)}.indexOf(p)<0)p=${JSON.stringify(defaultPreference)};` +
  `var r=document.documentElement;r.dataset.themePreference=p;` +
  `r.dataset.theme=p==='system'?(matchMedia(${JSON.stringify(darkQuery)}).matches?'dark':'light'):p}catch(e){}`;

const listeners = new Set<() => void>();

function apply(preference: ThemePreference) {
  const root = document.documentElement;
  root.dataset.themePreference = preference;
  root.dataset.theme =
    preference === 'system' ? (window.matchMedia(darkQuery).matches ? 'dark' : 'light') : preference;
}

export function readThemePreference(): ThemePreference {
  const value = document.documentElement.dataset.themePreference;
  return isThemePreference(value) ? value : defaultPreference;
}

export const serverThemePreference = (): ThemePreference => defaultPreference;

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
    if (readThemePreference() === 'system') apply('system');
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
