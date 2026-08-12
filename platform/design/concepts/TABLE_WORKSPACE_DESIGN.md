# Table workspace v23

Concept references:

- `table-workspace-desktop.png` — 1536×1024 desktop workbench.
- `table-workspace-mobile.png` — 390×844 mobile catalogue.

## Design system

- Visual language: dark neutral operational workspace with the existing semantic theme variables. Accent is reserved for the active navigation item, primary action, focus ring and current page.
- Typography: system sans-serif; 26 px maximum workspace title, 14 px body, 12 px context, 10–11 px table labels.
- Geometry: 7–9 px control radius, 1 px borders, no decorative cards or marketing surfaces.
- Density: 40–44 px controls; compact 66 px desktop header and 56 px mobile navigation header; card thumbnails reduced to 40×60 px.
- Navigation: persistent 232 px sidebar above 1120 px; overlay menu below 1121 px.
- Table: sticky header, first column and action column; visible horizontal scrollbar; mobile swipe hint; one pagination row.

## Interface copy

- Workspace: `Карты Полей сражений`, `1–50 из 1213`, `Добавить карту`.
- Search: `Название, ID, DBF, текст или механика`.
- Mobile disclosure: `Фильтры`.
- Orientation hint: `Проведите по таблице в сторону, чтобы увидеть остальные столбцы`.
- Primary table fields: `Карта`, `Card EN`, `CARD_ID`, `DBF`, `Категория`, `Таверна`, `Тип`, `Атака`, `Здоровье`, `В пуле`, `Действия`.

## Removed from the catalogue

- Default analytics dashboard and catalogue summary widgets.
- Duplicate top counters and shortcuts to statistics and Wiki.
- Intro eyebrow/subtitle and Wiki coverage widget.
- Inline add-card accordion.
- Duplicate bottom pagination.

Analytics, Wiki translations and card creation remain available as dedicated routes.
