// Realistic rows for the statistics modules of the local fixture (shapes taken from panel/tests/analytics_cli.php).
// Shares arrive in every spelling the backends use: numbers in percent units and strings such as "7.98%".
const classes = [
  'Death Knight',
  'Demon Hunter',
  'Druid',
  'Hunter',
  'Mage',
  'Paladin',
  'Priest',
  'Rogue',
  'Shaman',
  'Warlock',
  'Warrior',
];
const words = [
  'Aggro',
  'Control',
  'Midrange',
  'Tempo',
  'Combo',
  'Burn',
  'Handbuff',
  'Quest',
  'Mug',
  'Murloc',
];
const minions = [
  'Prisonguard',
  'Tidecaller',
  'Reef Scout',
  'Bog Warden',
  'Ember Imp',
  'Lantern Drake',
  'Rust Golem',
];
// Same numbers on every run: a screenshot or a failure is reproducible.
const seeded = seed => {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
};
const round = (value, digits = 2) => Math.round(value * 10 ** digits) / 10 ** digits;
const make = (count, seed, row) => {
  const random = seeded(seed);
  return Array.from({ length: count }, (_, i) => row(i, random));
};
const archetype = i => `${words[i % words.length]} ${classes[(i * 3) % classes.length]}`;
const asOf = '2026-11-19T11:00:02+00:00';
const date = { key: 'data_as_of', label: 'Данные на', value: asOf, type: 'date' };
const sum = (rows, key) => rows.reduce((acc, row) => acc + (Number(row[key]) || 0), 0);
const count = (label, value) => ({ key: 'rows', label, value, type: 'number' });
const games = value => ({ key: 'games', label: 'Игр в выборке', value, type: 'number' });
const column = (key, label, type = 'text') => ({ key, label, type });

const modules = {
  meta: () => {
    const rows = make(53, 11, (i, r) => ({
      archetype: archetype(i),
      winrate: i % 4 === 0 ? 54 : round(46 + r() * 11, 1),
      popularity: round(9.9 - i * 0.17 + r() * 0.1, 1),
      games: Math.round(8383 - i * 140 + r() * 90),
      turns: round(7 + r() * 3, 1),
      duration_minutes: round(7 + r() * 3, 1),
      climbing_speed: round(r(), 2),
      class_name: classes[(i * 3) % classes.length],
    }));
    return {
      columns: [
        column('archetype', 'Архетип'),
        column('winrate', 'Винрейт', 'percent'),
        column('popularity', 'Популярность', 'percent'),
        column('games', 'Игры', 'number'),
        column('turns', 'Ходы', 'number'),
        column('duration_minutes', 'Минуты', 'number'),
        column('climbing_speed', 'Скорость подъёма', 'number'),
      ],
      rows,
      total: 53,
      summary: [date, games(sum(rows, 'games')), count('Архетипов', 53)],
      hints: { period: { default: 'past_day', current: 'black_empire', current_patch: 'patch_37.0.2' } },
      options: {
        period: [
          'past_3_hours',
          'past_6_hours',
          'past_day',
          'past_3_days',
          'past_week',
          'past_2_weeks',
          'past_30_days',
          'patch_37.0.2',
          'black_empire',
          'black_empire_ban',
        ],
      },
    };
  },
  archetypes: () => {
    const rows = make(47, 12, (i, r) => ({
      name: archetype(i),
      class_name: classes[(i * 3) % classes.length],
      win_rate: round(55.83 - i * 0.2 + r() * 0.1),
      pct_of_total: round(7.96 - i * 0.15),
      pct_of_class: i % 5 ? round(88 - i * 0.7, 2) : 75,
      games: Math.round(21000 - i * 400),
    }));
    return {
      columns: [
        column('name', 'Архетип'),
        column('class_name', 'Класс'),
        column('win_rate', 'Винрейт', 'percent'),
        column('pct_of_total', 'Доля', 'percent'),
        column('pct_of_class', 'Доля в классе', 'percent'),
        column('games', 'Игры', 'number'),
      ],
      rows,
      total: 47,
      summary: [date, games(sum(rows, 'games')), count('Архетипов', 47)],
    };
  },
  hsguru_archetypes: () => {
    const rows = make(242, 13, (i, r) => ({
      name: archetype(i),
      class_name: classes[(i * 3) % classes.length],
      winrate: i === 0 ? 61 : round(61.3 - i * 0.12 + r() * 0.2, 1),
      popularity_pct: round(Math.max(0.05, 10.5 - i * 0.07), 2),
      top_deck_win_rate: i % 7 === 3 ? null : round(65 - i * 0.1, 1),
      games: Math.round(260000 - i * 900),
      decks: 1 + (i % 9),
    }));
    return {
      columns: [
        column('name', 'Архетип'),
        column('class_name', 'Класс'),
        column('winrate', 'Винрейт', 'percent'),
        column('popularity_pct', 'Популярность', 'percent'),
        column('top_deck_win_rate', 'Лучшая колода', 'percent'),
        column('games', 'Игры', 'number'),
        column('decks', 'Сборок', 'number'),
      ],
      rows,
      total: 242,
      summary: [
        date,
        games(sum(rows, 'games')),
        count('Архетипов', 242),
        { key: 'with_decks', label: 'Со сборками', value: 199, type: 'number' },
      ],
    };
  },
  decks: () => ({
    columns: [
      column('name', 'Колода'),
      column('class_name', 'Класс'),
      column('win_rate', 'Винрейт', 'percent'),
      column('games', 'Игры', 'number'),
      column('source_id', 'Источник', 'code'),
    ],
    rows: make(812, 14, (i, r) => ({
      name: `${archetype(i)} #${i + 1}`,
      class_name: classes[(i * 3) % classes.length],
      win_rate: i % 3 ? round(49 + r() * 4) : null,
      games: Math.round(900 - i),
      source_id: 'hsguru_decks',
    })),
    total: 812,
    summary: [date, { key: 'rows', label: 'Колод', value: 812, type: 'number' }],
  }),
  bg_heroes: () => {
    const rows = make(116, 15, (i, r) => ({
      name: `Герой ${i + 1}`,
      first_place: 18.85441527446301 - i * 0.021 + r() * 0.01,
      pick_rate_value: round(59.56 - i * 0.4),
      avg_placement: round(3.9 + i * 0.012, 2),
      games: Math.round(900 - i * 4),
    }));
    return {
      columns: [
        column('name', 'Герой'),
        column('first_place', 'Первое место', 'percent'),
        column('pick_rate_value', 'Выбор', 'percent'),
        column('avg_placement', 'Среднее место', 'number'),
        column('games', 'Игры', 'number'),
      ],
      rows,
      total: 116,
      summary: [date, games(sum(rows, 'games')), count('Героев', 116)],
      options: { mode: ['solo', 'duos'], rating: ['100', '50', '25', '10', '1'] },
    };
  },
  bg_minions: () => ({
    columns: [
      column('name', 'Существо'),
      column('tavern_tier', 'Таверна', 'number'),
      column('popularity', 'Популярность', 'percent'),
      column('combat_winrate', 'Винрейт боя', 'percent'),
      column('impact', 'Влияние', 'number'),
      column('avg_placement_with', 'Среднее место', 'number'),
      column('games_with_minion', 'Игры', 'number'),
    ],
    rows: make(250, 16, (i, r) => ({
      name: `${minions[i % minions.length]} ${1 + Math.floor(i / minions.length)}`,
      tavern_tier: 1 + (i % 6),
      popularity: i % 9 === 4 ? null : (7.98 - i * 0.03).toFixed(2) + '%',
      combat_winrate: (57.7 - i * 0.05).toFixed(2) + '%',
      impact: round(-0.07 + r() * 0.2),
      avg_placement_with: round(4.42 - r() * 0.3),
      games_with_minion: Math.round(13452 - i * 40),
    })),
    total: 250,
    summary: [date, { key: 'rows', label: 'Существ', value: 250, type: 'number' }],
  }),
  arena: () => {
    const rows = classes.map((name, i) => ({
      class_name: name,
      win_rate: round(52.6 - i * 0.2),
      pick_rate: round(80.58 - i * 3.1),
      pct_7_plus: round(9.45 - i * 0.2),
      games: 90000 - i * 3000,
    }));
    return {
      columns: [
        column('class_name', 'Класс'),
        column('win_rate', 'Винрейт', 'percent'),
        column('pick_rate', 'Выбор', 'percent'),
        column('pct_7_plus', '7+ побед', 'percent'),
        column('games', 'Игры', 'number'),
      ],
      rows,
      total: rows.length,
      summary: [date, count('Классов', rows.length)],
    };
  },
  arena_cards: () => ({
    columns: [
      column('name', 'Карта'),
      column('win_rate', 'Винрейт', 'percent'),
      column('drawn_winrate', 'Винрейт при взятии', 'percent'),
      column('mulligan_winrate', 'Винрейт после замены', 'percent'),
      column('games', 'Игры', 'number'),
    ],
    rows: make(1152, 17, (i, r) => ({
      name: `Карта арены ${i + 1}`,
      win_rate: round(71.93 - i * 0.03),
      drawn_winrate: (70.2 - i * 0.02).toFixed(1) + '%',
      mulligan_winrate: i % 8 === 5 ? '100.0%' : (68.8 + r() * 12).toFixed(1) + '%',
      games: Math.round(4000 - i),
    })),
    total: 1152,
    summary: [date, { key: 'rows', label: 'Карт', value: 1152, type: 'number' }],
    options: { arena_source: ['hsreplay', 'firestone', 'underground'] },
  }),
  // Answers like the old PHP: no keyed summary, so the panel counts the KPI itself.
  constructed_cards: () => ({
    columns: [
      column('name', 'Карта'),
      column('deck_winrate', 'Винрейт колод', 'percent'),
      column('deck_popularity', 'Популярность', 'percent'),
      column('keep_percentage', 'Оставляют', 'percent'),
      column('games', 'Игры', 'number'),
    ],
    rows: make(1281, 18, (i, r) => ({
      name: `Карта Standard ${i + 1}`,
      deck_winrate: (54.31 - i * 0.01).toFixed(2) + '%',
      deck_popularity: (11.5 - i * 0.005).toFixed(2) + '%',
      keep_percentage: (78.11 + r() * 20).toFixed(2) + '%',
      games: Math.round(31000 - i * 20),
    })),
    total: 1281,
    summary: [
      { label: 'Формат', value: 'standard' },
      { label: 'Записей', value: 200 },
    ],
    legacy: true,
  }),
  patches: () => ({
    columns: [
      column('patch', 'Патч'),
      column('released_at', 'Дата', 'date'),
      column('matched', 'Сопоставлено', 'number'),
    ],
    rows: make(322, 19, (i, r) => ({
      patch: `37.${Math.floor((322 - i) / 20)}.${(322 - i) % 20}`,
      released_at: new Date(Date.UTC(2026, 10, 19) - i * 86400000).toISOString(),
      matched: Math.round(r() * 40),
    })),
    total: 322,
    summary: [
      date,
      { label: 'Всего', value: 322, type: 'number' },
      { label: 'Сопоставлено', value: 200, type: 'number' },
    ],
  }),
  card: () => {
    const rows = ['Стандарт', 'Вольный'].map((format, i) => ({
      format,
      winrate: round(53.4 - i * 1.7, 1),
      popularity: i ? '4.20%' : '11.50%',
      games: 12345 - i * 6000,
    }));
    return {
      columns: [
        column('format', 'Формат'),
        column('winrate', 'Винрейт', 'percent'),
        column('popularity', 'Популярность', 'percent'),
        column('games', 'Игры', 'number'),
      ],
      rows,
      total: rows.length,
      summary: [{ label: 'Записей', value: rows.length }],
      legacy: true,
    };
  },
};

/** The answer of one module for the given query: filtered by `q`, cut to `limit`/`offset` when the module has them. */
export function moduleAnswer(module, definition, search) {
  const build = modules[module];
  if (!build) return null;
  const built = build(search);
  const needle = (search.get('q') || '').trim().toLowerCase();
  let rows = built.rows;
  if (needle)
    rows = rows.filter(row =>
      String(row.name ?? row.archetype ?? '')
        .toLowerCase()
        .includes(needle),
    );
  const total = needle ? rows.length : built.total;
  const limit = Math.min(
    Number(search.get('limit')) || definition.params.limit?.default || rows.length,
    definition.params.limit?.max || rows.length,
  );
  const offset = definition.params.offset ? Math.max(0, Number(search.get('offset')) || 0) : 0;
  const options = { ...built.options };
  for (const [name, rule] of Object.entries(definition.params))
    if (rule.type === 'enum' && !options[name]) options[name] = rule.values;
  return { ...built, rows: rows.slice(offset, offset + limit), total, options };
}
