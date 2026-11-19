# Class icons

Hearthstone class art (`<class>-64.webp`, `<class>-96.webp`; `all-96.webp` is the neutral one), copied unchanged from
HearthPulse: `public/class_icon/ui/` on `origin/main` of `Manacost-Labs/HeartPulse` (https://hearthpulse.net).
Slugs: `deathknight`, `demonhunter`, `druid`, `hunter`, `mage`, `paladin`, `priest`, `rogue`, `shaman`, `warlock`,
`warrior`. Imported by `components/class-icons.ts`, so Next serves them under `/_next/static/media/` — the only static path
nginx sends to panel-next (a file in `public/` would 404 on prod). Used by `components/ClassIcon.tsx`; the class comes from `lib/archetypes.ts`.
