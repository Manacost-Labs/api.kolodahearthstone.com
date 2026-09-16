const $ = (sel) => document.querySelector(sel);

const LABELS = {
  hsguru_meta_standard_legend: "Мета Standard · Legend",
  hsguru_meta_standard_diamond_4to1: "Мета Standard · Diamond",
  hsguru_meta_standard_top_5k: "Мета Standard · Top 5k",
  hsguru_meta_standard_top_legend: "Мета Standard · Top Legend",
  hsguru_meta_wild_legend: "Мета Wild · Legend",
  hsguru_meta_wild_diamond_4to1: "Мета Wild · Diamond",
  hsguru_meta_wild_top_legend: "Мета Wild · Top Legend",
  hsguru_meta_wild_top_5k: "Мета Wild · Top 5k",
  hsguru_matchups_legend: "Матчапы · Legend",
  hsguru_matchups_diamond_4to1: "Матчапы · Diamond",
  hsguru_streamer_decks_legend_1000: "Колоды стримеров",
  hsguru_fun_decks: "Фановые / off-meta колоды",
  hsreplay_arena: "Арена · матрица классов",
  hsreplay_arena_cards_advanced: "Арена · тир карт",
  hsreplay_arena_legendaries: "Арена · легендарные группы",
  hsreplay_arena_winning_decks: "Арена · виновые колоды",
  hsreplay_decks_trending: "Трендовые колоды",
  hsreplay_cards_legend_included_winrate: "Карты · winrate",
  hsreplay_cards_legend_included_popularity: "Карты · popularity",
  hsreplay_cards_legend_1d: "Карты · Legend · 1 день",
  hsreplay_cards_wild_legend_1d: "Карты Wild · Legend · 1 день",
  hsreplay_meta_archetypes_legend_eu_1d: "HSReplay · Архетипы Legend EU 1 день",
  hsreplay_meta_top_1000_legend_1d_firecrawl: "HSReplay · Meta Top 1000 Legend 1 день",
  hsreplay_meta_legend_1d_firecrawl: "HSReplay · Meta Legend 1 день",
  hsreplay_meta_diamond_4to1_1d_firecrawl: "HSReplay · Meta Diamond 4-1 1 день",
  hsreplay_arena_class_pages_firecrawl: "HSReplay · Arena классы Firecrawl",
  hsreplay_battlegrounds_minions: "BG · существа",
  hsreplay_battlegrounds_compositions: "BG · составы",
  firestone_battlegrounds_cards: "Firestone · BG существа по тавернам",
  firestone_battlegrounds_spells: "Firestone · BG заклинания по тавернам",
  firestone_battlegrounds_comps: "Firestone · BG компы",
  firestone_arena_cards_normal: "Firestone · Арена обычные карты",
  firestone_arena_cards_underground: "Firestone · Подпольная арена обычные карты",
  firestone_arena_legendaries_underground: "Firestone · Подпольная арена легендарки",
  firestone_arena_legendaries_normal: "Firestone · Арена легендарки",
  heartharena_tierlist: "HearthArena · Тир-лист карт",
  metastats_decks: "MetaStats · Архетипы и колоды",
  metastats_matchups: "MetaStats · Матрица матчапов",
  hearthstone_decks: "HS-Decks · Топ легенды (Std/Wild)",
  vicious_syndicate_live_beta: "vS · Data Reaper Live",
  vicious_syndicate_radars: "vS · Радар карт (Связи)",
  hsreplay_battlegrounds_comps: "BG · компы",
  hsreplay_battlegrounds_trinkets_lesser: "BG · малые тринкеты",
  hsreplay_battlegrounds_trinkets_greater: "BG · большие тринкеты",
};

/** Порядок кнопок в сайдбаре (остальные — в конце по id). */
const SIDEBAR_ORDER = [
  "hsguru_meta_standard_legend",
  "hsguru_meta_standard_diamond_4to1",
  "hsguru_meta_standard_top_5k",
  "hsguru_meta_standard_top_legend",
  "hsguru_meta_wild_legend",
  "hsguru_meta_wild_diamond_4to1",
  "hsguru_meta_wild_top_legend",
  "hsguru_meta_wild_top_5k",
  "hsguru_matchups_legend",
  "hsguru_matchups_diamond_4to1",
  "hsguru_streamer_decks_legend_1000",
  "hsguru_fun_decks",
];

const SIDEBAR_GROUPS = [
  { key: "hsguru", title: "HSGuru" },
  { key: "hsreplay", title: "HSReplay" },
  { key: "firestone", title: "Firestone" },
  { key: "heartharena", title: "HearthArena" },
  { key: "metastats", title: "MetaStats" },
  { key: "hearthstone-decks", title: "HS-Decks" },
  { key: "vicious-syndicate", title: "Vicious Syndicate" },
];

function sidebarSortIndex(sourceId) {
  const i = SIDEBAR_ORDER.indexOf(sourceId);
  return i >= 0 ? i : 1000;
}

function groupSources(sources) {
  const sorted = [...sources].sort(
    (a, b) =>
      sidebarSortIndex(a.source_id) - sidebarSortIndex(b.source_id) ||
      a.source_id.localeCompare(b.source_id)
  );
  const bySite = new Map();
  for (const s of sorted) {
    if (!bySite.has(s.site)) bySite.set(s.site, []);
    bySite.get(s.site).push(s);
  }
  const out = [];
  for (const g of SIDEBAR_GROUPS) {
    const items = bySite.get(g.key);
    if (items?.length) out.push({ title: g.title, sources: items });
    bySite.delete(g.key);
  }
  for (const [site, items] of bySite) {
    if (items.length) out.push({ title: site, sources: items });
  }
  return out;
}

async function loadOverview() {
  const res = await fetch("/demo/overview");
  const data = await res.json();
  const failCount = (data.total ?? 0) - (data.ok_count ?? 0);
  $("#stats").innerHTML =
    `<span class="stat-chip stat-chip--ok"><span class="stat-dot"></span>OK: <strong>${data.ok_count}</strong></span>` +
    (failCount > 0
      ? `<span class="stat-chip stat-chip--err"><span class="stat-dot"></span>Проблемных: <strong>${failCount}</strong></span>`
      : "") +
    `<span class="stat-chip">Всего: <strong>${data.total}</strong></span>` +
    `<a class="stat-chip stat-chip--link" href="/docs" target="_blank" rel="noopener">API ↗</a>`;

  const list = $("#source-list");
  list.innerHTML = "";

  // Добавляем вкладку быстрого поиска по базе SQLite сверху сайдбара
  const dbBtn = document.createElement("button");
  dbBtn.className = "source-btn";
  dbBtn.style.border = "1px solid #ff9f1c";
  dbBtn.style.boxShadow = "0 0 10px rgba(255, 159, 28, 0.15)";
  dbBtn.innerHTML = `
    <span class="id" style="color: #ff9f1c; font-weight: bold; display: flex; align-items: center; gap: 6px;">🔍 Поиск колод (SQLite)</span>
    <span class="meta">Быстрый поиск по всем колодам</span>
    <span class="badge ok" style="background: #ff9f1c; color: black; font-weight: bold;">local</span>
  `;
  dbBtn.onclick = () => selectDbSearch(dbBtn);
  list.appendChild(dbBtn);

  const archetypeBtn = document.createElement("button");
  archetypeBtn.className = "source-btn";
  archetypeBtn.style.border = "1px solid #6ea8fe";
  archetypeBtn.style.boxShadow = "0 0 10px rgba(110, 168, 254, 0.12)";
  archetypeBtn.innerHTML = `
    <span class="id" style="color: #9cc3ff; font-weight: bold;">Архетипы HSReplay</span>
    <span class="meta">Legend Standard · mulligan · matchups · decks</span>
    <span class="badge ok">sqlite</span>
  `;
  archetypeBtn.onclick = () => selectArchetypeDb(archetypeBtn);
  list.appendChild(archetypeBtn);

  const bgMinionsBtn = document.createElement("button");
  bgMinionsBtn.className = "source-btn";
  bgMinionsBtn.style.border = "1px solid #45d6a0";
  bgMinionsBtn.style.boxShadow = "0 0 10px rgba(69, 214, 160, 0.12)";
  bgMinionsBtn.innerHTML = `
    <span class="id" style="color: #7ee7bd; font-weight: bold;">BG существа · SQLite</span>
    <span class="meta">265 minions · графики · compositions screenshot</span>
    <span class="badge ok">firecrawl</span>
  `;
  bgMinionsBtn.onclick = () => selectBgMinionsDb(bgMinionsBtn);
  list.appendChild(bgMinionsBtn);

  const bgHeroesBtn = document.createElement("button");
  bgHeroesBtn.className = "source-btn";
  bgHeroesBtn.style.border = "1px solid #2ec4b6";
  bgHeroesBtn.style.boxShadow = "0 0 10px rgba(46, 196, 182, 0.12)";
  bgHeroesBtn.innerHTML = `
    <span class="id" style="color: #88f0e4; font-weight: bold;">BG герои · HSReplay</span>
    <span class="meta">тир-лист · таверна · hero power · дуо</span>
    <span class="badge ok">json api</span>
  `;
  bgHeroesBtn.onclick = () => selectBgHeroesDb(bgHeroesBtn);
  list.appendChild(bgHeroesBtn);

  const patchesBtn = document.createElement("button");
  patchesBtn.className = "source-btn";
  patchesBtn.style.border = "1px solid #f5b740";
  patchesBtn.style.boxShadow = "0 0 10px rgba(245, 183, 64, 0.12)";
  patchesBtn.innerHTML = `
    <span class="id" style="color: #ffd27a; font-weight: bold;">Патчи Hearthstone</span>
    <span class="meta">hs-manacost.ru · статьи обновлений</span>
    <span class="badge ok">sqlite</span>
  `;
  patchesBtn.onclick = () => selectPatchesDb(patchesBtn);
  list.appendChild(patchesBtn);

  for (const group of groupSources(data.sources)) {
    const heading = document.createElement("h3");
    heading.className = "source-group-title";
    heading.textContent = group.title;
    list.appendChild(heading);

    for (const s of group.sources) {
      const btn = document.createElement("button");
      btn.className = "source-btn";
      btn.dataset.id = s.source_id;
      const label = LABELS[s.source_id] || s.source_id;
      const cachedBadge = s.status?.serving_cached_dataset
        ? `<span class="badge err" title="Live refresh failed; serving last good dataset">cached</span>`
        : "";
      btn.innerHTML = `
      <span class="id">${label}</span>
      <span class="meta">${s.category}</span>
      <span class="badge ${s.state === "ok" ? "ok" : "err"}">${s.status?.effective_state || s.state}</span>${cachedBadge}
    `;
      btn.onclick = () => selectSource(s.source_id, btn);
      list.appendChild(btn);
    }
  }
}

async function selectSource(id, btn) {
  document.querySelectorAll(".source-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  $("#placeholder").classList.add("hidden");
  const detail = $("#detail");
  detail.classList.remove("hidden");
  detail.innerHTML = "<p>Загрузка…</p>";

  const res = await fetch(`/demo/view/${id}`);
  const payload = await res.json();
  detail.innerHTML = renderDetail(payload);
  
  if (id === "vicious_syndicate_radars" && payload.view) {
    initRadarGraph(payload.view);
  } else if (payload.view) {
    const t = viewType(payload.view);
    if (t === "meta" && payload.view.strategies) {
      initMetaScatterChart(payload.view.strategies);
    }
  }
}

function viewType(v) {
  return v.type || v.kind || "";
}

function renderCards(cards) {
  if (!cards || !cards.length) {
    return "<p class='muted'>Нет декодированных карт.</p>";
  }
  return `<ul class="cards-list">${cards
    .map(
      (c) =>
        `<li><strong>${c.count || 1}×</strong> ${escapeHtml(c.name)} — ` +
        `<code>id: ${c.id || "?"}</code> <code>dbfId: ${c.dbfId ?? "?"}</code></li>`
    )
    .join("")}</ul>`;
}

function renderTableFromObjects(rows, columns, options = {}) {
  if (!rows.length) return "<p class='muted'>Нет строк.</p>";
  const cols = columns || Object.keys(rows[0]);
  const visibleRows = options.limit === null ? rows : rows.slice(0, options.limit || 50);
  let html = "<table class='simple'><thead><tr>";
  for (const c of cols) html += `<th>${escapeHtml(c)}</th>`;
  html += "</tr></thead><tbody>";
  for (const row of visibleRows) {
    html += "<tr>";
    for (const c of cols) html += `<td>${escapeHtml(String(row[c] ?? ""))}</td>`;
    html += "</tr>";
  }
  html += "</tbody></table>";
  return html;
}

function isWeakText(value) {
  const text = String(value || "").trim();
  return !text || text.length < 20 || text.endsWith(":") || text === "Вы" || text === "После";
}

function cleanGuideText(value) {
  return String(value || "")
    .replace(/\[\[([^|\]]+)(?:\|\|[^\]]+)?\]\]/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function renderTrinketCards(rows) {
  if (!rows.length) return "<p class='muted'>Нет строк.</p>";
  return `<div class="trinket-grid">${rows
    .map((x) => {
      const description = isWeakText(x.description) ? "" : cleanGuideText(x.description);
      const guide = cleanGuideText(x.guide);
      const shownDescription = description || guide || "Описание недоступно в источнике";
      const dist = Array.isArray(x.placement_distribution)
        ? x.placement_distribution
            .slice(0, 4)
            .map((p) => `${p.place}: ${p.rate}`)
            .join(" · ")
        : "";
      const fullDist = Array.isArray(x.placement_distribution)
        ? x.placement_distribution.map((p) => `${p.place}: ${p.rate}`).join(" · ")
        : "";
      return `<article class="trinket-card">
        <div class="trinket-card__head">
          <div>
            <h5>${escapeHtml(x.name || "Unknown trinket")}${x.tribe ? ` · ${escapeHtml(x.tribe)}` : ""}</h5>
            <p>${escapeHtml([x.localized_name, x.tribe_ru].filter(Boolean).join(" · "))}</p>
          </div>
          <span class="badge ok">${escapeHtml([x.trinket_tier || x.type, x.tier ? `Tier ${x.tier}` : ""].filter(Boolean).join(" · "))}</span>
        </div>
        <div class="trinket-card__stats">
          <span>Cost <b>${escapeHtml(String(x.cost ?? ""))}</b></span>
          <span>Pick <b>${escapeHtml(x.pick_rate || "—")}</b></span>
          <span>Avg <b>${escapeHtml(x.avg_placement || "—")}</b></span>
          <span>1st <b>${escapeHtml(Array.isArray(x.placement_distribution) ? (x.placement_distribution.find((p) => p.place === 1)?.rate || "—") : "—")}</b></span>
        </div>
        <p class="trinket-card__desc">${escapeHtml(shownDescription)}</p>
        ${guide && guide !== shownDescription ? `<p class="trinket-card__guide"><b>Guide:</b> ${escapeHtml(guide)}</p>` : ""}
        ${dist ? `<p class="trinket-card__dist"><b>Места:</b> ${escapeHtml(dist)}</p>` : ""}
        ${fullDist && fullDist !== dist ? `<details><summary>Полное распределение</summary><p class="trinket-card__dist">${escapeHtml(fullDist)}</p></details>` : ""}
        <code>${escapeHtml(x.trinket_id || x.id || "")}</code>
      </article>`;
    })
    .join("")}</div>`;
}

function renderCardStatsTable(cards) {
  return renderTableFromObjects(
    cards.map((c) => ({
      Карта: c.name,
      id: c.id || "",
      "В % колодах": c.deck_popularity || c.pick_rate || "",
      Копий: c.avg_copies || "",
      "Винрейт колод": c.deck_winrate || "",
      "Сыграно игр": c.times_played || "",
      Взятие: c.winrate_when_drawn || "",
      "Побед с картой": c.winrate_when_played || "",
      Оставили: c.keep_percentage || "",
      "Turn held": c.avg_turns_in_hand ?? "",
      "Turn played": c.avg_turn_played_on ?? "",
      dbfId: c.dbfId ?? "",
    })),
    [
      "Карта",
      "id",
      "В % колодах",
      "Копий",
      "Винрейт колод",
      "Сыграно игр",
      "Взятие",
      "Побед с картой",
      "Оставили",
      "Turn held",
      "Turn played",
      "dbfId",
    ]
  );
}

function renderDetail(p) {
  if (!p.ok) return `<h2>Ошибка</h2><p>${escapeHtml(p.message || "")}</p>`;

  const v = p.view;
  const t = viewType(v);
  let body = "";

  if (t === "arena_legendary_groups" && v.groups) {
    body = `<div class="block"><h3>Легендарные группы (${v.groups.length})</h3>`;
    for (const g of v.groups) {
      const key = g.key_card || g.legendary_card || {};
      body += `<div class="strategy"><h4>${escapeHtml(key.name || key.card_id || "Легендарка")}</h4>
        <p class="muted">${escapeHtml(g.class || "")} · id: ${escapeHtml(key.id || "?")} · dbfId: ${escapeHtml(String(key.dbfId ?? "?"))}</p>
        <p>Winrate: <strong>${escapeHtml(g.winrate || "?")}</strong>${g.pick_rate ? " · Pick: " + escapeHtml(g.pick_rate) : ""}${g.offer_rate ? " · Offer: " + escapeHtml(g.offer_rate) : ""}</p>
        <p><strong>Карты пакета</strong></p>${renderCards(g.cards)}</div>`;
    }
    body += "</div>";
  } else if (t === "arena_winning_decks" && v.decks) {
    const feedNote = v.total_decks && v.total_decks !== v.decks.length
      ? ` · в ленте ${v.total_decks}`
      : v.new_unique_decks
        ? ` · +${v.new_unique_decks} новых`
        : "";
    body = `<div class="block"><h3>Виновые колоды арены (${v.decks.length}${feedNote})</h3>`;
    if (v.fetched_this_run) {
      body += `<p class="muted">Последний refresh: ${escapeHtml(String(v.fetched_this_run))} с API, лимит ленты ${escapeHtml(String(v.feed_cap || 500))}</p>`;
    }
    for (const d of v.decks) {
      const cls = d.main_class || d.class || "?";
      body += `<div class="strategy"><h4>${escapeHtml(cls)} · ${escapeHtml(d.record || "?")} · ${escapeHtml(d.player || "?")}</h4>
        <p class="muted">${escapeHtml(d.played_at || "")}${d.region ? " · " + escapeHtml(d.region) : ""}${d.draft_id ? " · draft " + escapeHtml(String(d.draft_id)) : ""}${d.legendary_group ? " · Группа: " + escapeHtml(d.legendary_group) : ""}</p>
        ${d.hero_power_class ? `<p class="muted">Hero power: ${escapeHtml(d.hero_power_class)}</p>` : ""}
        <p><strong>Финальная колода</strong> (${(d.final_deck || []).reduce((s, c) => s + (c.count || 1), 0)} карт, ${(d.final_deck || []).length} уник.)</p>${renderCards(d.final_deck || [])}
        <p><strong>Сброшено (redraft)</strong></p>${renderCards(d.discarded || d.redraft?.discarded || [])}
        <p><strong>Добавлено (redraft)</strong></p>${renderCards(d.added || d.redraft?.added || [])}
        ${d.package_cards?.length ? `<p><strong>Легендарный пакет</strong></p>${renderCards(d.package_cards)}` : ""}</div>`;
    }
    body += "</div>";
  } else if (t === "trending_decks" && v.decks) {
    body = `<div class="block"><h3>Трендовые колоды (${v.decks.length})</h3>`;
    body += renderTableFromObjects(
      v.decks.map((d) => ({
        Колодa: d.name,
        Winrate: d.winrate,
        Games: d.games,
        Duration: d.duration || "",
        "HSReplay ID": d.hsreplay_deck_id,
      })),
      ["Колодa", "Winrate", "Games", "Duration", "HSReplay ID"]
    );
    body += "</div>";
  } else if (t === "bg_heroes" && v.blocked) {
    body = `<div class="block warn"><h3>Герои BG не загрузились</h3>
      <p>HSReplay вернул «Повторите попытку позже». Обновите источник или проверьте сессию.</p></div>`;
  } else if (t === "bg_heroes" && v.heroes) {
    body = `<div class="block"><h3>Герои Battlegrounds (${v.heroes.length})</h3>`;
    if (v.time_period || v.mmr) {
      body += `<p class="muted">Период: ${escapeHtml(v.time_period || "?")} · MMR: ${escapeHtml(v.mmr || "?")}${v.last_update ? " · обновлено Firestone: " + escapeHtml(String(v.last_update)) : ""}</p>`;
    }
    body += renderTableFromObjects(
      v.heroes.map((h) => ({
        Герой: h.hero,
        Тир: h.tier || "",
        "Лучший состав": h.best_comp || h.best_tribe || "",
        "Среднее место": h.avg_placement ?? h.average_position ?? "",
        "Pick rate": h.pick_rate || "",
        "Распределение мест": (h.placement_distribution || []).join(" · "),
        Игры: h.games ?? h.data_points ?? "",
        "id / dbfId": `${h.hero_card_id || h.id || "?"} / ${h.dbfId ?? "?"}`,
      })),
      ["Герой", "Тир", "Лучший состав", "Среднее место", "Pick rate", "Распределение мест", "Игры", "id / dbfId"]
    );
    body += "</div>";
  } else if (t === "bg_minions" && v.minions) {
    body = `<div class="block"><h3>Существа Battlegrounds (${v.minions.length})</h3>`;
    body += renderTableFromObjects(
      v.minions.map((m) => ({
        Существо: m.minion || m.name,
        id: m.id || "",
        "Влияние на игру": m.impact ?? "",
        "Доля побед": m.win_share || m.combat_winrate || "",
        Популярность: m.popularity || "",
        "Уровень таверны": m.tavern_tier ?? "",
        dbfId: m.dbfId ?? m.minion_dbf_id ?? "",
      })),
      ["Существо", "id", "Влияние на игру", "Доля побед", "Популярность", "Уровень таверны", "dbfId"]
    );
    body += "</div>";
  } else if (t === "bg_compositions" && v.compositions) {
    body = `<div class="block"><h3>Составы Battlegrounds (${v.compositions.length})</h3>`;
    body += renderTableFromObjects(
      v.compositions.map((c) => ({
        "Тип существ": c.type,
        "Первое место": c.first_place || "",
        "Средняя позиция": c.avg_placement ?? "",
        Популярность: c.popularity || "",
        "Распределение мест": (c.placement_distribution || []).join(" · "),
        Игры: c.games ?? "",
      })),
      ["Тип существ", "Первое место", "Средняя позиция", "Популярность", "Распределение мест", "Игры"]
    );
    body += "</div>";
  } else if (t === "vicious_live") {
    body = `<div class="block"><h3>Vicious Syndicate Live · ${escapeHtml(v.format || "Standard")}</h3>`;
    body += `<p class="muted">Pie chart: ${escapeHtml(v.pie_time_range || "?")} · games: ${escapeHtml(String(v.games || "?"))}. Tier-list: ladder ${escapeHtml(v.tier_ladder_time_range || "?")} + matchup ${escapeHtml(v.tier_matchup_time_range || "?")}.</p>`;
    if (v.class_distribution?.length) {
      body += `<h4>Распределение классов</h4>`;
      body += renderTableFromObjects(
        v.class_distribution.map((c) => ({
          Класс: c.class,
          Частота: c.frequency || "",
        })),
        ["Класс", "Частота"]
      );
    }
    if (v.deck_distribution?.length) {
      body += `<h4>Распределение колод</h4>`;
      body += renderTableFromObjects(
        v.deck_distribution.map((d) => ({
          Колода: d.deck,
          Класс: d.class || "",
          Частота: d.frequency || "",
        })),
        ["Колода", "Класс", "Частота"]
      );
    }
    if (v.tier_list?.length) {
      body += `<h4>Power Tier List</h4>`;
      for (const bracket of v.tier_list) {
        body += `<div class="strategy" style="scroll-margin-top: 20px;">`;
        body += `<h4>${escapeHtml(bracket.rank_bracket || "")}</h4>`;
        body += renderTableFromObjects(
          (bracket.decks || []).map((d) => ({
            Место: d.rank,
            Колода: d.deck,
            Winrate: d.winrate || "",
          })),
          ["Место", "Колода", "Winrate"]
        );
        body += `</div>`;
      }
    }
    body += "</div>";
  } else if (t === "arena_class_matrix") {
    body = `<div class="block"><h3>Арена · классы и матчапы</h3>`;
    if (v.classes?.length) {
      body += `<h4>Винрейт классов (${v.classes.length})</h4>`;
      body += renderTableFromObjects(
        v.classes.map((c) => ({
          Класс: c.class,
          Winrate: c.winrate || c.win_rate,
          Drafts: c.num_drafts ?? "",
          "Pick %": c.pick_rate ?? "",
          "7+ wins %": c.pct_7_plus ?? "",
        })),
        ["Класс", "Winrate", "Drafts", "Pick %", "7+ wins %"]
      );
    }
    if (v.matchups?.length) {
      body += `<h4>Двухклассовые матчапы (${v.matchups.length})</h4>`;
      if (v.matchups[0]?.class_a) {
        body += renderTableFromObjects(v.matchups.slice(0, 80), ["class_a", "class_b", "winrate"]);
      } else {
        body += renderTableFromObjects(v.matchups.slice(0, 25));
      }
    }
    body += "</div>";
  } else if (t === "arena_class_pages" && v.classes) {
    body = `<div class="block"><h3>Arena классы (${v.classes.length})</h3>`;
    if (v.source) {
      body += `<p class="muted">Backend: ${escapeHtml(v.source.backend || "")} · Firecrawl ok: ${escapeHtml(String(v.source.firecrawl_ok ?? ""))}/${escapeHtml(String(v.source.classes ?? ""))}</p>`;
    }
    body += renderTableFromObjects(
      v.classes.map((c) => ({
        Класс: c.class_ru || c.class_name || c.class,
        "Class URL": c.url || "",
        Winrate: c.winrate || (c.win_rate != null ? `${Number(c.win_rate).toFixed(2)}%` : ""),
        "7+ побед": c.pct_7_plus != null ? `${Number(c.pct_7_plus).toFixed(2)}%` : "",
        "Pick rate": c.pick_rate != null ? `${Number(c.pick_rate).toFixed(2)}%` : "",
        Заходы: c.num_drafts ?? "",
        Firecrawl: c.firecrawl?.ok ? "ok" : (c.firecrawl?.error || "—"),
      })),
      ["Класс", "Winrate", "7+ побед", "Pick rate", "Заходы", "Firecrawl"]
    );
    body += "</div>";
  } else if (t === "matchups" && v.matchups && v.matchups.length) {
    body = `<div class="block"><h3>Матрица матчапов (${v.matchups.length} ячеек)</h3>`;
    body += renderTableFromObjects(
      v.matchups.slice(0, 100).map((m) => ({
        Архетип: m.archetype,
        Против: m.vs,
        Winrate: m.winrate,
      })),
      ["Архетип", "Против", "Winrate"]
    );
    if (v.matchups.length > 100) {
      body += `<p class="muted">Показаны первые 100. API: <code>/datasets/${escapeHtml(p.source_id)}</code></p>`;
    }
    body += "</div>";
  } else if (t === "meta" && v.strategies) {
    body = `<div class="block">
      <h3>Распределение архетипов (Винрейт / Популярность)</h3>
      <p class="muted" style="margin-bottom: 15px;">Интерактивный график мета-отчета. По вертикали — популярность колоды (%), по горизонтали — процент побед (%). Наведите курсор на точку, чтобы подсветить ее и увидеть подробности.</p>
      
      <div class="canvas-container" style="background: #111113; border-radius: 8px; padding: 10px; margin-bottom: 15px; border: 1px solid var(--border);">
        <canvas id="meta-scatter-canvas" width="850" height="500" style="background: #1e1e24; border-radius: 6px; width: 100%; max-width: 850px; aspect-ratio: 850/500; display: block; margin: 0 auto;"></canvas>
      </div>
      <div id="meta-hover-info" class="meta-hover-info" style="min-height: 36px; padding: 8px 12px; background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 6px; text-align: center; color: #f0f0f0; margin-bottom: 25px; font-size: 0.95rem;">
        Наведите на точку, чтобы увидеть детали
      </div>

      <h3>Таблица архетипов и статистик (${v.strategies.length})</h3>`;
    body += renderTableFromObjects(v.strategies);
    body += "</div>";
  } else if (t === "streamer_decks" && v.decks) {
    body = `<div class="block"><h3>Колоды стримеров (${v.decks.length})</h3>`;
    for (const d of v.decks) {
      body += `<div class="strategy"><h4>${escapeHtml(d.strategy)}</h4>
        <p>${escapeHtml(d.streamer || "")} · ${escapeHtml(d.format || "")} · ${escapeHtml(d.record || "")}</p>
        ${renderCards(d.cards)}</div>`;
    }
    body += "</div>";
  } else if ((t === "hsguru_fun_decks" || t === "fun_decks") && Array.isArray(v.rows)) {
    const rows = v.rows;
    const byFormat = rows.reduce((acc, row) => {
      const key = String(row.format || "other");
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
    body = `<div class="block"><h3>Фановые / off-meta колоды (${rows.length})</h3>`;
    body += `<p class="muted">Детектор: ${escapeHtml(String(v.detector_version || "?"))} · Standard ${escapeHtml(String(byFormat.Standard || byFormat.standard || 0))} · Wild ${escapeHtml(String(byFormat.Wild || byFormat.wild || 0))}</p>`;
    body += "<table class='simple'><thead><tr><th>Название</th><th>Формат</th><th>Класс</th><th>Fun</th><th>Sim</th><th>WR</th><th>Код</th><th>Причины</th></tr></thead><tbody>";
    for (const row of rows.slice(0, 100)) {
      const code = String(row.deck_code || "");
      const reasons = Array.isArray(row.reasons) ? row.reasons.join(", ") : "";
      body += `<tr>
        <td>${row.url ? `<a href="${escapeHtml(row.url)}" target="_blank" rel="noopener">${escapeHtml(row.title || "?")}</a>` : escapeHtml(row.title || "?")}</td>
        <td>${escapeHtml(row.format || "")}</td>
        <td>${escapeHtml(row.class || "")}</td>
        <td>${escapeHtml(String(row.fun_score ?? ""))}</td>
        <td>${escapeHtml(String(row.max_meta_similarity ?? ""))}</td>
        <td>${escapeHtml(String(row.win_rate ?? ""))}</td>
        <td><code style="user-select:all;word-break:break-all;">${escapeHtml(code)}</code></td>
        <td>${escapeHtml(reasons)}</td>
      </tr>`;
    }
    body += "</tbody></table></div>";
  } else if (t === "card_stats" && v.blocked) {
    body = `<div class="block warn"><h3>Требуется сессия HSReplay</h3>
      <p>Импортируйте cookies и выполните refresh.</p></div>`;
  } else if (t === "card_stats" && v.cards && v.cards.length) {
    body = `<div class="block"><h3>Карты (${v.cards.length})</h3>${renderCardStatsTable(v.cards)}</div>`;
  } else if (t === "hsreplay_meta_archetypes" && v.classes) {
    body = `<div class="block"><h3>HSReplay · Архетипы по классам</h3>`;
    if (v.filters) {
      body += `<p class="muted">${escapeHtml(v.filters.rank_range || "?")} · ${escapeHtml(v.filters.region || "?")} · ${escapeHtml(v.filters.time_range || "?")}${v.as_of ? " · as of: " + escapeHtml(String(v.as_of)) : ""}</p>`;
    }
    for (const classGroup of v.classes) {
      const rows = classGroup.archetypes || [];
      body += `<div class="strategy" style="scroll-margin-top: 20px;">`;
      body += `<h4>${escapeHtml(classGroup.class_name || classGroup.class || "?")} (${rows.length} арх., ${escapeHtml(String(classGroup.games || 0))} игр)</h4>`;
      body += renderTableFromObjects(
        rows.map((a) => ({
          Архетип: a.archetype,
          Winrate: a.winrate || "",
          Популярность: a.popularity || "",
          "В классе": a.class_popularity || "",
          Игры: a.games ?? "",
          id: a.archetype_id ?? "",
        })),
        ["Архетип", "Winrate", "Популярность", "В классе", "Игры", "id"]
      );
      body += `</div>`;
    }
    body += "</div>";
  } else if (t === "arena_card_tiers" && v.cards && v.cards.length) {
    body = `<div class="block"><h3>Тир-лист арены (${v.cards.length} карт)</h3>`;
    if (v.total_cards) body += `<p class="muted">Всего в базе: ${escapeHtml(v.total_cards)}</p>`;
    body += renderTableFromObjects(
      v.cards.map((c) => ({
        "Название карты": c.name,
        id: c.id || c.card_id || "",
        "Винрейт колоды": c.deck_winrate || "",
        Взятие: c.winrate_when_drawn || "",
        "Played Winrate": c.winrate_when_played || "",
        "In % Runs": c.in_runs || (c.popularity !== null && c.popularity !== undefined ? `${c.popularity}%` : ""),
        "Avg Copies": c.avg_copies ?? "",
        "Всего партий": c.times_played ?? "",
        "Arenasmith Score": c.score ?? "",
        "Частота выбора": c.pick_rate !== null && c.pick_rate !== undefined ? `${c.pick_rate}%` : "",
        dbfId: c.dbfId ?? "",
      })),
      [
        "Название карты",
        "id",
        "Винрейт колоды",
        "Взятие",
        "Played Winrate",
        "In % Runs",
        "Avg Copies",
        "Всего партий",
        "Arenasmith Score",
        "Частота выбора",
        "dbfId",
      ]
    );
    body += "</div>";
  } else if (t === "heartharena_tierlist" && v.classes) {
    body = `<div class="block"><h3>Оценка карт HearthArena (${v.total_cards} карт)</h3>`;
    const validClasses = v.classes.filter((c) => c.cards && c.cards.length);
    if (validClasses.length > 1) {
      body += `<p class="muted">Быстрый переход: `;
      body += validClasses
        .map((c) => `<a href="#class-${c.class_id}">${escapeHtml(c.class_name)}</a>`)
        .join(" · ");
      body += `</p>`;
    }
    for (const cls of validClasses) {
      body += `<div id="class-${cls.class_id}" class="strategy" style="scroll-margin-top: 20px;">`;
      body += `<h4>${escapeHtml(cls.class_name)} (${cls.total_cards} шт.)</h4>`;
      body += renderTableFromObjects(
        cls.cards.map((c) => ({
          Карта: c.name,
          "Оценка (Score)": c.score ?? "",
          Тир: c.tier_name || "",
          Мана: c.cost ?? "",
          Тип: c.type ?? "",
          Редкость: c.rarity ?? "",
          "id / dbfId": `${c.card_id || c.id || "?"} / ${c.dbfId ?? "?"}`,
        })),
        ["Карта", "Оценка (Score)", "Тир", "Мана", "Тип", "Редкость", "id / dbfId"]
      );
      body += `</div>`;
    }
    body += "</div>";
  } else if (t === "bg_trinkets" && v.trinkets && v.trinkets.length) {
    const withStats = v.trinkets.filter((x) => x.pick_rate || x.avg_placement);
    const withoutStats = v.trinkets.filter((x) => !x.pick_rate && !x.avg_placement);
    const toRow = (x) => ({
      Тринкет: x.name || "",
      "RU название": x.localized_name || "",
      Раса: x.tribe || "",
      "Раса RU": x.tribe_ru || "",
      Тип: x.trinket_tier || x.type || "",
      Тир: x.tier || "",
      Cost: x.cost ?? "",
      "Pick Rate": x.pick_rate || "",
      "Avg placement": x.avg_placement || "",
      "1 место": Array.isArray(x.placement_distribution) ? (x.placement_distribution.find((p) => p.place === 1)?.rate || "") : "",
      ID: x.trinket_id || x.id || "",
    });
    body = `<div class="block"><h3>Активные тринкеты (${withStats.length})</h3>`;
    body += `<p class="muted">Показываем только аксессуары из текущего пула HSReplay: у них есть pick rate / avg placement. Неактивные канонические записи скрыты: ${withoutStats.length}.</p>`;
    body += `<h4>Лучшие по среднему месту</h4>`;
    body += renderTrinketCards(
      withStats
        .slice()
        .sort((a, b) => Number(a.avg_placement || 99) - Number(b.avg_placement || 99))
        .slice(0, 24)
    );
    body += `<h4>Таблица со статистикой (${withStats.length})</h4>`;
    body += renderTableFromObjects(
      withStats.map(toRow),
      ["Тринкет", "RU название", "Раса", "Раса RU", "Тип", "Тир", "Cost", "Pick Rate", "Avg placement", "1 место", "ID"],
      { limit: null }
    );
    body += "</div>";
  } else if (t === "bg_card_stats" && v.tiers) {
    body = `<div class="block"><h3>Статистика по тавернам</h3>`;
    if (v.total_data_points || v.last_update_date) {
      body += `<p class="muted">Всего игр: ${escapeHtml(v.total_data_points || "?")} · Обновлено Firestone: ${escapeHtml(String(v.last_update_date || "?"))}</p>`;
    }
    const validTiers = Object.keys(v.tiers).filter((t) => v.tiers[t] && v.tiers[t].length);
    if (validTiers.length > 1) {
      body += `<p class="muted">Быстрый переход: `;
      body += validTiers
        .map((t) => {
          const name = t === "other" ? "Разное" : `Т${t}`;
          return `<a href="#tier-${t}">${name}</a>`;
        })
        .join(" · ");
      body += `</p>`;
    }
    for (const tierName of validTiers) {
      const tierCards = v.tiers[tierName];
      const tierTitle = tierName === "other" ? "Разное (other)" : `Уровень таверны ${tierName}`;
      body += `<div id="tier-${tierName}" class="strategy" style="scroll-margin-top: 20px;">`;
      body += `<h4>${escapeHtml(tierTitle)} (${tierCards.length} шт.)</h4>`;
      body += renderTableFromObjects(
        tierCards.map((c) => {
          let imp = "";
          if (c.impact !== null && c.impact !== undefined) {
            const num = Number(c.impact);
            if (!isNaN(num)) {
              imp = num > 0 ? `+${num.toFixed(3)}` : num.toFixed(3);
            }
          }
          return {
            Карта: c.name,
            "Игр сыграно": c.total_played ?? "",
            "Среднее место": c.average_placement ?? "",
            "Вне колоды": c.average_placement_other ?? "",
            "Влияние (Impact)": imp,
            "id / dbfId": `${c.card_id || c.id || "?"} / ${c.dbfId ?? "?"}`,
          };
        }),
        ["Карта", "Игр сыграно", "Среднее место", "Вне колоды", "Влияние (Impact)", "id / dbfId"]
      );
      body += `</div>`;
    }
    body += "</div>";
  } else if (t === "bg_comps" && v.comps && v.comps.length) {
    body = `<div class="block"><h3>Компы Battlegrounds (${v.comps.length})</h3>`;
    for (const c of v.comps) {
      const title = c.strategy_title || c.title || c.name || c.slug || "?";
      const meta = [
        c.tier ? `Тир ${c.tier}` : "",
        c.difficulty ? `Сложность: ${c.difficulty}` : "",
        `#${c.comp_id || c.source_id || ""}`,
      ].filter(Boolean).join(" · ");
      body += `<div class="strategy"><h4>${escapeHtml(title)} <span class="muted">${escapeHtml(meta)}</span></h4>`;
      if (c.url) body += `<p class="muted"><a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.url)}</a></p>`;
      if (c.main_cards?.length) {
        body += `<p><strong>Ключевые карты (${c.main_cards.length})</strong></p>${renderCards(c.main_cards)}`;
      }
      if (c.additional_cards?.length) {
        body += `<p><strong>Дополнительные карты (${c.additional_cards.length})</strong></p>${renderCards(c.additional_cards)}`;
      }
      if (c.when_to_commit_cards?.length) {
        body += `<p><strong>Когда выходить в стратегию (${c.when_to_commit_cards.length})</strong></p>${renderCards(c.when_to_commit_cards)}`;
      }
      if (c.enabler_cards?.length) {
        body += `<p><strong>Открывают стратегию (${c.enabler_cards.length})</strong></p>${renderCards(c.enabler_cards)}`;
      }
      body += "</div>";
    }
    body += "</div>";
  } else if (t === "metastats_decks" && v.decks) {
    body = `<div class="block"><h3>Архетипы и колоды MetaStats (${v.total_decks} колод)</h3>`;
    const classes = [...new Set(v.decks.map(d => d.class))];
    body += `<p class="muted">Фильтр по классам: `;
    body += classes.map(cls => `<a href="#class-meta-${cls}">${escapeHtml(cls)}</a>`).join(" · ");
    body += `</p>`;
    
    const decksByClass = {};
    for (const d of v.decks) {
      if (!decksByClass[d.class]) decksByClass[d.class] = [];
      decksByClass[d.class].push(d);
    }
    
    for (const cls of classes) {
      const clsDecks = decksByClass[cls];
      body += `<div id="class-meta-${cls}" style="scroll-margin-top: 20px; margin-bottom: 2rem;">`;
      body += `<h3 style="border-bottom: 2px solid var(--border); padding-bottom: 0.5rem; color: var(--accent);">${escapeHtml(cls)}</h3>`;
      
      const decksByArch = {};
      for (const d of clsDecks) {
        if (!decksByArch[d.archetype_name]) decksByArch[d.archetype_name] = [];
        decksByArch[d.archetype_name].push(d);
      }
      
      for (const archName in decksByArch) {
        const archDecks = decksByArch[archName];
        body += `<div class="strategy" style="margin-bottom: 1.5rem;">`;
        body += `<h4 style="font-size: 1.15rem; color: #ffbc42; margin-bottom: 0.75rem;">${escapeHtml(archName)}</h4>`;
        
        for (const d of archDecks) {
          body += `<div style="background: rgba(0,0,0,0.2); padding: 0.75rem; border-radius: 6px; margin-bottom: 0.75rem; border-left: 3px solid var(--accent);">`;
          body += `<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; margin-bottom: 0.5rem;">`;
          body += `<span style="font-weight: bold; font-size: 0.9rem;">Версия #${escapeHtml(d.deck_id)}</span>`;
          body += `<span class="muted" style="font-size: 0.85rem;">Игр: <strong>${d.games || "?"}</strong> · Winrate: <strong style="color: var(--ok);">${escapeHtml(d.win_rate || "?")}</strong></span>`;
          body += `</div>`;
          
          if (d.deck_code) {
            body += `<div style="margin-bottom: 0.5rem; display: flex; gap: 0.5rem; align-items: center;">`;
            body += `<input type="text" readonly value="${escapeHtml(d.deck_code)}" style="background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.2rem 0.4rem; font-size: 0.75rem; border-radius: 4px; flex-grow: 1; font-family: monospace;" onclick="this.select();" />`;
            body += `<button onclick="navigator.clipboard.writeText('${escapeHtml(d.deck_code)}'); alert('Код скопирован в буфер!');" class="source-btn" style="padding: 0.2rem 0.5rem; font-size: 0.75rem; background: var(--panel);">Скопировать</button>`;
            body += `</div>`;
          }
          
          if (d.cards && d.cards.length) {
            body += `<details style="font-size: 0.85rem;"><summary class="muted" style="cursor: pointer; user-select: none;">Показать карты (${d.cards.reduce((sum, c) => sum + (c.count || 1), 0)} шт.)</summary>`;
            body += `<div style="margin-top: 0.5rem; padding-left: 0.5rem;">`;
            body += `<ul class="cards-list" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 0.25rem 1rem;">`;
            body += d.cards.map(c => `<li><strong>${c.count || 1}×</strong> ${escapeHtml(c.name)} <code style="font-size: 0.7rem; color: var(--muted);">${c.id || ""}</code></li>`).join("");
            body += `</ul></div></details>`;
          }
          body += `</div>`;
        }
        body += `</div>`;
      }
      body += `</div>`;
    }
    body += "</div>";
  } else if (t === "metastats_matchups" && v.matchups) {
    body = `<div class="block"><h3>Матрица матчапов MetaStats (${v.matchups.length} ячеек)</h3>`;
    body += renderTableFromObjects(
      v.matchups.map((m) => ({
        Архетип: m.archetype,
        Против: m.vs,
        Игр: m.games ?? "",
        "Винрейт (WR)": m.winrate || "",
        "Оппонент WR": m.vs_winrate || "",
      })),
      ["Архетип", "Против", "Игр", "Винрейт (WR)", "Оппонент WR"]
    );
    body += "</div>";
  } else if (t === "hearthstone_decks" && v.decks) {
    body = `<div class="block"><h3>Топ легенды Standard и Wild (${v.total_decks} колод)</h3>`;
    body += `<p class="muted">Фильтр по форматам: <a href="#format-Standard">Standard (${v.standard_count || 0})</a> · <a href="#format-Wild">Wild (${v.wild_count || 0})</a></p>`;
    
    const formats = ["Standard", "Wild"];
    for (const fmt of formats) {
      const fmtDecks = v.decks.filter(d => d.format === fmt);
      if (!fmtDecks.length) continue;
      
      body += `<div id="format-${fmt}" style="scroll-margin-top: 20px; margin-bottom: 2rem;">`;
      body += `<h3 style="border-bottom: 2px solid var(--border); padding-bottom: 0.5rem; color: #ff9f1c;">${escapeHtml(fmt)} Decks</h3>`;
      
      let html = "<table class='simple'><thead><tr>";
      html += "<th>Архетип</th><th>Ранг</th><th>Игрок</th><th>Счет</th><th>Код колоды</th><th>Дата</th><th>Ссылка</th>";
      html += "</tr></thead><tbody>";
      
      for (const d of fmtDecks) {
        html += "<tr>";
        html += `<td><strong>${escapeHtml(d.archetype || d.title)}</strong></td>`;
        html += `<td>${escapeHtml(d.rank || "")}</td>`;
        html += `<td>${escapeHtml(d.player || "")}</td>`;
        html += `<td>${escapeHtml(d.score || "")}</td>`;
        
        if (d.deck_code) {
          html += `<td>
            <div style="display: flex; gap: 4px; align-items: center;">
              <input type="text" readonly value="${escapeHtml(d.deck_code)}" style="background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.15rem 0.3rem; font-size: 0.72rem; border-radius: 4px; width: 140px; font-family: monospace;" onclick="this.select();" />
              <button onclick="navigator.clipboard.writeText('${escapeHtml(d.deck_code)}'); alert('Код колоды скопирован!');" class="source-btn" style="padding: 0.15rem 0.3rem; font-size: 0.7rem; background: var(--panel);">Копировать</button>
            </div>
          </td>`;
        } else {
          html += "<td><span class='muted'>Загрузка / Нет кода</span></td>";
        }
        
        html += `<td>${escapeHtml(d.date || "")}</td>`;
        html += `<td><a href="${escapeHtml(d.url)}" target="_blank" class="source-btn" style="padding: 0.15rem 0.4rem; font-size: 0.72rem; text-decoration: none; color: inherit; display: inline-block;">Открыть</a></td>`;
        html += "</tr>";
      }
      html += "</tbody></table>";
      body += html;
      body += `</div>`;
    }
    body += "</div>";
  } else if (t === "vicious_syndicate_radars" && v.radars) {
    const classItems = v.classes_summary || Array.from(new Set(v.radars.map(r => r.class))).map(cls => ({
      class: cls,
      has_archetypes: v.radars.some(r => r.class === cls && r.archetype !== null)
    }));
    body = `<div class="block">
      <h3>Интерактивный радар карт Vicious Syndicate (Выпуск #${escapeHtml(v.issue || "?")})</h3>
      <p class="muted" style="margin-bottom: 1.5rem;">Граф показывает связи между картами в колодах. Чем больше круг, тем популярнее карта. Чем толще линия, тем сильнее синергия.</p>
      
      <!-- Class Selector Tabs -->
      <h5 style="margin: 0 0 0.5rem 0; font-size: 0.95rem;">Выберите класс:</h5>
      <div class="tabs-container" style="display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 1rem;">
        ${classItems.map((item, i) => {
          const cls = item.class;
          const hasArch = item.has_archetypes;
          return `
            <button class="source-btn class-tab-btn ${i === 0 ? "active" : ""}" data-class="${escapeHtml(cls)}" style="font-weight: bold; border-radius: 6px; padding: 0.4rem 0.8rem; border: 1px solid var(--border); display: flex; align-items: center; gap: 6px;">
              ${escapeHtml(cls)}
              ${hasArch ? `<span style="background: #2ec4b6; color: #fff; font-size: 0.65rem; padding: 0.1rem 0.35rem; border-radius: 10px; font-weight: normal;">+ Архетипы</span>` : ""}
            </button>
          `;
        }).join("")}
      </div>

      <!-- Archetype Selector Tabs -->
      <h5 style="margin: 0 0 0.5rem 0; font-size: 0.95rem;">Выберите архетип / радар:</h5>
      <div id="radar-archetypes-container" style="display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 1.5rem; background: var(--panel); padding: 0.4rem; border-radius: 8px; border: 1px dashed var(--border);">
        <!-- Populated dynamically -->
      </div>

      <!-- Selected Radar Deck Code Block -->
      <div id="radar-deck-code-section" class="block" style="background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 1.2rem; margin-bottom: 1.5rem; display: none;">
        <h4 style="margin-top: 0; color: #ff9f1c; font-size: 1.1rem; margin-bottom: 0.5rem;">Код колоды для этого радара</h4>
        <div style="display: flex; gap: 8px; align-items: center;">
          <input type="text" id="radar-deck-code-input" readonly style="background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.5rem 0.8rem; font-size: 0.85rem; border-radius: 6px; width: 100%; font-family: monospace;" onclick="this.select();" />
          <button id="radar-deck-code-copy-btn" class="source-btn" style="padding: 0.5rem 1rem; border-radius: 6px; font-weight: bold; background: #2ec4b6; color: white;">Копировать</button>
        </div>
      </div>

      <!-- Graph Container -->
      <div style="position: relative; margin: 1.5rem 0; background: #0f0f11; border: 1px solid var(--border); border-radius: 12px; padding: 1rem; overflow: hidden;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; gap: 10px;">
          <div style="display: flex; gap: 8px; align-items: center;">
            <input type="text" id="radar-search" placeholder="Поиск карты..." style="background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.4rem 0.8rem; border-radius: 6px; font-size: 0.9rem; width: 220px;" />
            <button id="radar-reset-btn" class="source-btn" style="padding: 0.4rem 0.8rem; border-radius: 6px;">Сброс</button>
          </div>
          <div id="radar-hover-info" class="muted" style="font-size: 0.85rem; min-height: 1.2rem;">Наведите на карту для деталей</div>
        </div>
        
        <canvas id="radar-canvas" width="750" height="750" style="background: #111113; border-radius: 8px; width: 100%; max-width: 750px; aspect-ratio: 1; display: block; margin: 0 auto; cursor: grab;"></canvas>
      </div>

      <!-- Selected Card Details & Table -->
      <div style="display: grid; grid-template-columns: 1fr; gap: 1.5rem; margin-top: 1.5rem;">
        <div class="block" style="background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 1.2rem;">
          <h4 id="selected-card-title" style="margin-top: 0; color: #ff9f1c; font-size: 1.1rem;">Выберите карту на графе</h4>
          <div id="selected-card-details">
            <p class="muted">Нажмите на любой узел на графе, чтобы увидеть его сильные связи с другими картами.</p>
          </div>
        </div>
        
        <div class="block" style="background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 1.2rem;">
          <h4 style="margin-top: 0; margin-bottom: 1rem; font-size: 1.1rem;">Список карт в радаре</h4>
          <div style="max-height: 350px; overflow-y: auto;">
            <table class="simple" id="radar-nodes-table">
              <thead>
                <tr>
                  <th>Название карты</th>
                  <th>Популярность (Радиус)</th>
                  <th>Связей</th>
                </tr>
              </thead>
              <tbody>
                <!-- Populated dynamically -->
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>`;
  } else if (t === "bg_comps") {
    body = `<div class="block warn"><h3>Компы BG</h3>
      <p>Данные не загрузились. Нужна сессия HSReplay и refresh.</p></div>`;
  } else if (t === "bg_page" && v.items) {
    body = `<div class="block"><ul class="lines">${v.items.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul></div>`;
  } else if (v.lines) {
    body = `<div class="block"><ul class="lines">${v.lines.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul></div>`;
  }

  return `
    <h2>${escapeHtml(LABELS[p.source_id] || p.source_id)}</h2>
    <p class="meta-line">
      ${escapeHtml(p.site)} · ${escapeHtml(p.category)} · обновлено: <time datetime="${escapeHtml(p.fetched_at || "")}" title="${escapeHtml(p.fetched_at || "")}">${escapeHtml(formatDateRu(p.fetched_at))}</time>
      · <a href="${escapeHtml(p.url)}" target="_blank">источник</a>
    </p>
    ${body || "<p class='muted'>Нет структурированных данных.</p>"}
  `;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const RU_MONTHS = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

// "2026-06-08T07:10:22.780533+00:00" -> "8 июня в 10:10" (в местном времени браузера)
function formatDateRu(value) {
  if (value == null || value === "") return "?";
  const d = new Date(value);
  if (isNaN(d.getTime())) return String(value);
  const now = new Date();
  const day = d.getDate();
  const month = RU_MONTHS[d.getMonth()];
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const yearPart = d.getFullYear() === now.getFullYear() ? "" : ` ${d.getFullYear()}`;

  const startOfDay = (dt) => new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime();
  const dayDiff = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  let dayLabel = `${day} ${month}${yearPart}`;
  if (dayDiff === 0) dayLabel = "сегодня";
  else if (dayDiff === 1) dayLabel = "вчера";

  return `${dayLabel} в ${hh}:${mm}`;
}

async function loadTechStack() {
  const tbody = document.getElementById("tech-table-body");
  if (!tbody) return;
  try {
    const res = await fetch("/system/technologies");
    const data = await res.json();
    const rows = data.technologies || [];
    tbody.innerHTML = rows
      .map((t) => {
        const name = t.link
          ? `<a href="${t.link}" target="_blank" rel="noopener">${escapeHtml(t.name)}</a>`
          : escapeHtml(t.name);
        const notes = t.notes ? `<div class="muted">${escapeHtml(t.notes)}</div>` : "";
        const statusClass = `tech-status tech-status--${(t.status || "optional").replace(/\s+/g, "-")}`;
        return `<tr>
          <td>${name}${notes}</td>
          <td>${escapeHtml(t.role)}</td>
          <td>${escapeHtml(t.layer)}</td>
          <td><span class="${statusClass}">${escapeHtml(t.status)}</span></td>
        </tr>`;
      })
      .join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">Не удалось загрузить: ${escapeHtml(String(e))}</td></tr>`;
  }
}

loadTechStack().catch(() => {});
loadOverview().catch((e) => {
  $("#stats").textContent = "Ошибка: " + e.message;
});

let radarAnimationId = null;

function initRadarGraph(data) {
  if (radarAnimationId) {
    cancelAnimationFrame(radarAnimationId);
    radarAnimationId = null;
  }

  const canvas = document.getElementById("radar-canvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const searchInput = document.getElementById("radar-search");
  const resetBtn = document.getElementById("radar-reset-btn");
  const hoverInfo = document.getElementById("radar-hover-info");
  const selectedCardTitle = document.getElementById("selected-card-title");
  const selectedCardDetails = document.getElementById("selected-card-details");
  const nodesTableBody = document.querySelector("#radar-nodes-table tbody");
  const archetypesContainer = document.getElementById("radar-archetypes-container");

  let currentClass = data.radars[0] ? data.radars[0].class : null;
  let currentArchetype = null;
  let nodes = [];
  let edges = [];
  let draggedNode = null;
  let hoveredNode = null;
  let selectedNode = null;
  let searchText = "";

  function rebuildArchetypeTabs(clsName) {
    if (!archetypesContainer) return;
    archetypesContainer.innerHTML = "";

    const classRadars = data.radars.filter(r => r.class === clsName);
    
    // Sort radars so that the main class radar (archetype === null) is first
    classRadars.sort((a, b) => {
      if (a.archetype === null) return -1;
      if (b.archetype === null) return 1;
      return a.archetype.localeCompare(b.archetype);
    });

    classRadars.forEach((r, i) => {
      const btn = document.createElement("button");
      btn.className = `source-btn arch-tab-btn ${i === 0 ? "active" : ""}`;
      btn.style.fontSize = "0.8rem";
      btn.style.padding = "0.3rem 0.6rem";
      btn.style.borderRadius = "4px";
      btn.style.border = "1px solid var(--border)";
      btn.textContent = r.archetype ? r.archetype : "Общий (Класс)";
      
      btn.addEventListener("click", () => {
        document.querySelectorAll(".arch-tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentArchetype = r.archetype;
        loadClassRadar(currentClass, currentArchetype);
      });

      archetypesContainer.appendChild(btn);
    });
  }

  document.querySelectorAll(".class-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".class-tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentClass = btn.dataset.class;
      currentArchetype = null;
      rebuildArchetypeTabs(currentClass);
      loadClassRadar(currentClass, null);
    });
  });

  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      searchText = e.target.value.toLowerCase().trim();
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      if (searchInput) searchInput.value = "";
      searchText = "";
      selectedNode = null;
      hoveredNode = null;
      updateSelectedCardView();
      loadClassRadar(currentClass, currentArchetype);
    });
  }

  function loadClassRadar(clsName, archetype = null) {
    const radar = data.radars.find(r => r.class === clsName && r.archetype === archetype);
    if (!radar) return;

    selectedNode = null;
    hoveredNode = null;
    updateSelectedCardView();

    // Display deck code if available
    const deckCodeSec = document.getElementById("radar-deck-code-section");
    const deckCodeInp = document.getElementById("radar-deck-code-input");
    const deckCodeBtn = document.getElementById("radar-deck-code-copy-btn");
    
    if (deckCodeSec && deckCodeInp && deckCodeBtn) {
      if (radar.deck_code) {
        deckCodeInp.value = radar.deck_code;
        deckCodeSec.style.display = "block";
        deckCodeBtn.onclick = () => {
          navigator.clipboard.writeText(radar.deck_code);
          alert("Код колоды скопирован!");
        };
      } else {
        deckCodeSec.style.display = "none";
      }
    }

    nodes = radar.nodes.map(n => {
      const angle = Math.random() * Math.PI * 2;
      const radius = 150 + Math.random() * 100;
      return {
        ...n,
        x: 375 + Math.cos(angle) * radius,
        y: 375 + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        linksCount: radar.edges.filter(e => e.source === n.name || e.target === n.name).length
      };
    });

    edges = radar.edges.map(e => ({ ...e }));
    updateNodesTable();
  }

  function updateNodesTable() {
    if (!nodesTableBody) return;
    nodesTableBody.innerHTML = "";
    
    const sortedNodes = [...nodes].sort((a, b) => b.radius - a.radius);
    
    for (const n of sortedNodes) {
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.innerHTML = `
        <td><strong>${escapeHtml(n.name)}</strong></td>
        <td>${n.radius.toFixed(1)}</td>
        <td>${n.linksCount}</td>
      `;
      tr.addEventListener("click", () => {
        const matchingNode = nodes.find(node => node.name === n.name);
        if (matchingNode) {
          selectedNode = matchingNode;
          updateSelectedCardView();
        }
      });
      nodesTableBody.appendChild(tr);
    }
  }

  function updateSelectedCardView() {
    if (!selectedCardTitle || !selectedCardDetails) return;
    
    if (!selectedNode) {
      selectedCardTitle.textContent = "Выберите карту на графе";
      selectedCardDetails.innerHTML = `<p class="muted">Нажмите на любой узел на графе, чтобы увидеть его сильные связи с другими картами.</p>`;
      return;
    }

    selectedCardTitle.textContent = selectedNode.name;
    
    const conn = edges
      .filter(e => e.source === selectedNode.name || e.target === selectedNode.name)
      .map(e => {
        const other = e.source === selectedNode.name ? e.target : e.source;
        return { name: other, weight: e.weight };
      })
      .sort((a, b) => b.weight - a.weight)
      .slice(0, 10);

    let html = `<p><strong>Популярность (радиус):</strong> ${selectedNode.radius.toFixed(1)}</p>`;
    html += `<h5 style="margin: 0.8rem 0 0.4rem 0; border-bottom: 1px solid var(--border); padding-bottom: 0.2rem;">Топ-10 сильнейших связей:</h5>`;
    if (conn.length === 0) {
      html += `<p class="muted">Связи не найдены.</p>`;
    } else {
      html += `<ul style="margin: 0; padding-left: 1.2rem; line-height: 1.5; font-size: 0.9rem;">`;
      for (const c of conn) {
        html += `<li><strong>${escapeHtml(c.name)}</strong> (сила связи: ${(c.weight * 100).toFixed(0)}%)</li>`;
      }
      html += `</ul>`;
    }

    selectedCardDetails.innerHTML = html;
  }

  function getMousePos(evt) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (evt.clientX - rect.left) * (750 / rect.width),
      y: (evt.clientY - rect.top) * (750 / rect.height)
    };
  }

  canvas.addEventListener("mousedown", (e) => {
    const pos = getMousePos(e);
    let clicked = null;
    for (const n of nodes) {
      const dx = n.x - pos.x;
      const dy = n.y - pos.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist <= n.radius + 5) {
        clicked = n;
        break;
      }
    }

    if (clicked) {
      draggedNode = clicked;
      selectedNode = clicked;
      updateSelectedCardView();
      canvas.style.cursor = "grabbing";
    }
  });

  canvas.addEventListener("mousemove", (e) => {
    const pos = getMousePos(e);
    
    if (draggedNode) {
      draggedNode.x = pos.x;
      draggedNode.y = pos.y;
      draggedNode.vx = 0;
      draggedNode.vy = 0;
    } else {
      let found = null;
      for (const n of nodes) {
        const dx = n.x - pos.x;
        const dy = n.y - pos.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist <= n.radius + 5) {
          found = n;
          break;
        }
      }

      hoveredNode = found;
      if (found) {
        canvas.style.cursor = "pointer";
        if (hoverInfo) {
          hoverInfo.innerHTML = `Карта: <strong>${escapeHtml(found.name)}</strong> · Популярность: ${found.radius.toFixed(1)} · Связей: ${found.linksCount}`;
        }
      } else {
        canvas.style.cursor = "grab";
        if (hoverInfo) {
          hoverInfo.textContent = "Наведите на карту для деталей";
        }
      }
    }
  });

  window.addEventListener("mouseup", () => {
    if (draggedNode) {
      draggedNode = null;
      canvas.style.cursor = "grab";
    }
  });

  canvas.addEventListener("touchstart", (e) => {
    if (e.touches.length > 0) {
      const touch = e.touches[0];
      const mouseEvent = new MouseEvent("mousedown", {
        clientX: touch.clientX,
        clientY: touch.clientY
      });
      canvas.dispatchEvent(mouseEvent);
    }
  });

  canvas.addEventListener("touchmove", (e) => {
    if (e.touches.length > 0) {
      const touch = e.touches[0];
      const mouseEvent = new MouseEvent("mousemove", {
        clientX: touch.clientX,
        clientY: touch.clientY
      });
      canvas.dispatchEvent(mouseEvent);
    }
  });

  canvas.addEventListener("touchend", () => {
    const mouseEvent = new MouseEvent("mouseup", {});
    window.dispatchEvent(mouseEvent);
  });

  function tick() {
    const centerX = 375;
    const centerY = 375;
    
    for (let i = 0; i < nodes.length; i++) {
      const n1 = nodes[i];
      const cdx = centerX - n1.x;
      const cdy = centerY - n1.y;
      const cdist = Math.sqrt(cdx * cdx + cdy * cdy) || 1;
      n1.vx += (cdx / cdist) * 0.15;
      n1.vy += (cdy / cdist) * 0.15;

      for (let j = i + 1; j < nodes.length; j++) {
        const n2 = nodes[j];
        const dx = n2.x - n1.x;
        const dy = n2.y - n1.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const minDist = n1.radius + n2.radius + 35;
        if (dist < minDist) {
          const force = (minDist - dist) * 0.4;
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          n1.vx -= fx;
          n1.vy -= fy;
          n2.vx += fx;
          n2.vy += fy;
        }
      }
    }

    for (const e of edges) {
      const sourceNode = nodes.find(n => n.name === e.source);
      const targetNode = nodes.find(n => n.name === e.target);
      if (sourceNode && targetNode) {
        const dx = targetNode.x - sourceNode.x;
        const dy = targetNode.y - sourceNode.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        
        if (e.weight > 0.1) {
          const desiredLen = e.length || 220;
          const k = 0.015 * e.weight;
          const force = (dist - desiredLen) * k;
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          sourceNode.vx += fx;
          sourceNode.vy += fy;
          targetNode.vx -= fx;
          targetNode.vy -= fy;
        }
      }
    }

    for (const n of nodes) {
      if (n === draggedNode) continue;

      n.vx *= 0.85;
      n.vy *= 0.85;

      n.x += n.vx;
      n.y += n.vy;

      const margin = n.radius + 10;
      if (n.x < margin) { n.x = margin; n.vx = -n.vx * 0.5; }
      if (n.x > 750 - margin) { n.x = 750 - margin; n.vx = -n.vx * 0.5; }
      if (n.y < margin) { n.y = margin; n.vy = -n.vy * 0.5; }
      if (n.y > 750 - margin) { n.y = 750 - margin; n.vy = -n.vy * 0.5; }
    }

    ctx.clearRect(0, 0, 750, 750);

    for (const e of edges) {
      const sourceNode = nodes.find(n => n.name === e.source);
      const targetNode = nodes.find(n => n.name === e.target);
      if (!sourceNode || !targetNode) continue;

      let opacity = e.weight * 0.5;
      let isHighlighted = false;

      if (searchText) {
        const sMatch = sourceNode.name.toLowerCase().includes(searchText);
        const tMatch = targetNode.name.toLowerCase().includes(searchText);
        if (sMatch && tMatch) {
          isHighlighted = true;
          opacity = Math.min(1.0, e.weight * 1.5);
        } else {
          opacity = 0.02;
        }
      } else if (selectedNode) {
        if (sourceNode.name === selectedNode.name || targetNode.name === selectedNode.name) {
          isHighlighted = true;
          opacity = Math.min(0.9, e.weight * 1.8);
        } else {
          opacity = 0.02;
        }
      } else {
        if (e.weight < 0.15) continue;
      }

      ctx.beginPath();
      ctx.moveTo(sourceNode.x, sourceNode.y);
      ctx.lineTo(targetNode.x, targetNode.y);
      ctx.lineWidth = isHighlighted ? 2.5 : 1.0;
      ctx.strokeStyle = isHighlighted ? `rgba(255, 159, 28, ${opacity})` : `rgba(255, 255, 255, ${opacity})`;
      ctx.stroke();
    }

    for (const n of nodes) {
      let isHighlighted = true;
      let drawBorder = false;

      if (searchText) {
        isHighlighted = n.name.toLowerCase().includes(searchText);
      } else if (selectedNode) {
        isHighlighted = (n.name === selectedNode.name || edges.some(e => 
          (e.source === selectedNode.name && e.target === n.name) || 
          (e.target === selectedNode.name && e.source === n.name)
        ));
        drawBorder = (n.name === selectedNode.name);
      }

      const baseAlpha = isHighlighted ? 0.9 : 0.2;
      const strokeAlpha = isHighlighted ? 1.0 : 0.2;

      let fillStyle = n.fill || "rgba(0,102,0,0.75)";
      let strokeStyle = n.stroke || "rgba(221,221,221,1.00)";
      
      fillStyle = fillStyle.replace(/[\d.]+\)$/, `${baseAlpha})`);
      strokeStyle = strokeStyle.replace(/[\d.]+\)$/, `${strokeAlpha})`);

      if (drawBorder) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius + 6, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(255, 159, 28, 0.3)";
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
      ctx.fillStyle = fillStyle;
      ctx.fill();
      ctx.lineWidth = n.strokewidth || 2.0;
      ctx.strokeStyle = drawBorder ? "#ff9f1c" : strokeStyle;
      ctx.stroke();

      ctx.font = `bold ${n.radius < 10 ? 10 : 12}px sans-serif`;
      ctx.fillStyle = isHighlighted ? "rgba(255,255,255,1.00)" : "rgba(255,255,255,0.25)";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      
      ctx.shadowColor = "rgba(0, 0, 0, 0.9)";
      ctx.shadowBlur = 4;
      ctx.fillText(n.name, n.x, n.y);
      ctx.shadowBlur = 0;
    }

    radarAnimationId = requestAnimationFrame(tick);
  }

  rebuildArchetypeTabs(currentClass);
  loadClassRadar(currentClass, null);
  tick();
}


async function selectPatchesDb(btn) {
  document.querySelectorAll(".source-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  $("#placeholder").classList.add("hidden");
  const detail = $("#detail");
  detail.classList.remove("hidden");
  detail.innerHTML = `
    <h2>Патчи Hearthstone</h2>
    <p class="meta-line">Локальная SQLite-база статей hs-manacost.ru, сопоставленная с версиями из Hearthstone Wiki.</p>
    <div class="block patch-controls">
      <label>
        <span>Поиск</span>
        <input id="patch-query" type="text" placeholder="35.6, Поля сражений, баланс..." />
      </label>
      <button id="patch-search-btn" class="mini-action">Обновить</button>
    </div>
    <div id="patches-results" class="block"><p>Загрузка...</p></div>
  `;
  $("#patch-search-btn").onclick = loadPatchesList;
  $("#patch-query").onkeydown = (e) => {
    if (e.key === "Enter") loadPatchesList();
  };
  await loadPatchesList();
}

async function loadPatchesList() {
  const box = $("#patches-results");
  if (!box) return;
  box.innerHTML = "<p>Загрузка патчей...</p>";
  const q = $("#patch-query")?.value.trim() || "";
  let url = "/api/patches?limit=500&include_content=false";
  if (q) url += `&q=${encodeURIComponent(q)}`;
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "API error");
    let html = `
      <div class="archetype-db-head">
        <div>
          <h3>Патчи (${escapeHtml(String(data.total || 0))})</h3>
          <p class="muted">Таблица: значения Hearthstone Wiki и найденная статья hs-manacost.ru.</p>
        </div>
        <a href="/api/patches?limit=500" target="_blank" rel="noopener">JSON</a>
      </div>
    `;
    if (!data.patches?.length) {
      box.innerHTML = html + `<p class="muted">Патчи не найдены.</p>`;
      return;
    }
    html += `<div class="table-scroll"><table class="simple patches-table"><thead><tr>
      <th>Wiki</th>
      <th>Manacost</th>
      <th>Дата</th>
      <th>Статья</th>
    </tr></thead><tbody>`;
    for (const patch of data.patches) {
      const manacostVersion = patch.hs_manacost_version || "";
      const wikiVersion = patch.version || "";
      const articleUrl = patch.source_url || "";
      const wikiUrl = patch.wiki_url || "";
      const detailVersion = manacostVersion || wikiVersion;
      const detailUrl = `/api/patches/${encodeURIComponent(detailVersion)}?include_content=false`;
      const rowTitle = patch.title || patch.wiki_title || `Patch ${wikiVersion}`;
      html += `<tr>
        <td>
          <strong>${escapeHtml(wikiVersion)}</strong>
          ${wikiUrl ? `<div><a href="${escapeHtml(wikiUrl)}" target="_blank" rel="noopener">wiki.gg</a></div>` : ""}
        </td>
        <td>${manacostVersion ? `<strong>${escapeHtml(manacostVersion)}</strong><div><span class="badge ok">matched</span></div>` : `<span class="muted">не найдено</span><div><span class="badge err">wiki-only</span></div>`}</td>
        <td>${escapeHtml(formatDateRu(patch.published_at))}</td>
        <td>
          <a class="patch-title-link" href="${detailUrl}" target="_blank" rel="noopener">${escapeHtml(rowTitle)}</a>
          ${patch.summary ? `<div class="muted patch-summary">${escapeHtml(patch.summary)}</div>` : ""}
          ${articleUrl ? `<div style="margin-top: 0.35rem;"><a class="mini-action patch-article-link" href="${escapeHtml(articleUrl)}" target="_blank" rel="noopener">Статья hs-manacost.ru</a></div>` : `<div class="muted" style="margin-top: 0.35rem;">Статья hs-manacost.ru пока не сопоставлена</div>`}
        </td>
      </tr>`;
    }
    html += "</tbody></table></div>";
    box.innerHTML = html;
  } catch (err) {
    box.innerHTML = `<p class="muted" style="color: var(--err);">Ошибка загрузки: ${escapeHtml(err.message)}</p>`;
  }
}


async function selectDbSearch(btn) {
  document.querySelectorAll(".source-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  $("#placeholder").classList.add("hidden");
  const detail = $("#detail");
  detail.classList.remove("hidden");
  detail.innerHTML = `
    <h2>🔍 Поиск колод в базе SQLite</h2>
    <p class="meta-line">Поиск в реальном времени по всей истории собранных колод</p>
    
    <div class="block" style="background: var(--panel); border: 1px solid var(--border); padding: 1.5rem; border-radius: 8px; margin-bottom: 1.5rem;">
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 1.5rem;">
        <div>
          <label style="display: block; font-size: 0.85rem; margin-bottom: 0.4rem; color: #aaa;">Класс:</label>
          <select id="db-search-class" style="width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.5rem; border-radius: 6px;">
            <option value="">Все классы</option>
            <option value="DeathKnight">Рыцарь смерти (Death Knight)</option>
            <option value="DemonHunter">Охотник на демонов (Demon Hunter)</option>
            <option value="Druid">Друид (Druid)</option>
            <option value="Hunter">Охотник (Hunter)</option>
            <option value="Mage">Маг (Mage)</option>
            <option value="Paladin">Паладин (Paladin)</option>
            <option value="Priest">Жрец (Priest)</option>
            <option value="Rogue">Разбойник (Rogue)</option>
            <option value="Shaman">Шаман (Shaman)</option>
            <option value="Warlock">Чернокнижник (Warlock)</option>
            <option value="Warrior">Воин (Warrior)</option>
          </select>
        </div>
        <div>
          <label style="display: block; font-size: 0.85rem; margin-bottom: 0.4rem; color: #aaa;">Формат:</label>
          <select id="db-search-format" style="width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.5rem; border-radius: 6px;">
            <option value="">Все форматы</option>
            <option value="Standard">Стандартный (Standard)</option>
            <option value="Wild">Вольный (Wild)</option>
            <option value="Arena">Арена (Arena)</option>
          </select>
        </div>
        <div>
          <label style="display: block; font-size: 0.85rem; margin-bottom: 0.4rem; color: #aaa;">Источник:</label>
          <select id="db-search-source" style="width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.5rem; border-radius: 6px;">
            <option value="">Все источники</option>
            <option value="hearthstone_decks">Hearthstone-Decks.net</option>
            <option value="metastats_decks">MetaStats.net</option>
            <option value="vicious_syndicate_radars">Vicious Syndicate Radars</option>
          </select>
        </div>
      </div>
      
      <div style="display: flex; gap: 10px; align-items: center;">
        <input type="text" id="db-search-query" placeholder="Название колоды, архетип или код..." style="background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.6rem 1rem; border-radius: 6px; font-size: 0.95rem; flex: 1;" />
        <button id="db-search-btn" class="source-btn" style="background: #ff9f1c; color: #000; font-weight: bold; border: none; padding: 0.6rem 1.5rem; border-radius: 6px; cursor: pointer;">Найти</button>
      </div>
    </div>
    
    <div id="db-search-results" class="block">
      <p class="muted">Нажмите кнопку «Найти» или введите запрос для поиска колод.</p>
    </div>
  `;

  // Attach search events
  $("#db-search-btn").onclick = performDbSearch;
  $("#db-search-query").onkeydown = (e) => {
    if (e.key === "Enter") performDbSearch();
  };

  performDbSearch();
}

async function performDbSearch() {
  const resultsDiv = $("#db-search-results");
  if (!resultsDiv) return;

  resultsDiv.innerHTML = "<p>Поиск…</p>";

  const cls = $("#db-search-class").value;
  const fmt = $("#db-search-format").value;
  const src = $("#db-search-source").value;
  const q = $("#db-search-query").value.trim();

  let url = "/api/db/decks?limit=100";
  if (cls) url += `&class_name=${encodeURIComponent(cls)}`;
  if (fmt) url += `&format_name=${encodeURIComponent(fmt)}`;
  if (src) url += `&source_id=${encodeURIComponent(src)}`;
  if (q) url += `&q=${encodeURIComponent(q)}`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    
    if (!data.decks || data.decks.length === 0) {
      resultsDiv.innerHTML = `<p class="muted" style="text-align: center; padding: 2rem;">Колоды не найдены по вашему запросу.</p>`;
      return;
    }

    let html = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
        <h4 style="margin: 0;">Результаты поиска (${data.total}):</h4>
        <span class="muted" style="font-size: 0.85rem;">Показаны последние 100 колод</span>
      </div>
      <div style="overflow-x: auto;">
        <table class="simple">
          <thead>
            <tr>
              <th>Класс</th>
              <th>Архетип / Название</th>
              <th>Формат</th>
              <th>Показатель</th>
              <th>Источник</th>
              <th>Код колоды</th>
            </tr>
          </thead>
          <tbody>
    `;

    data.decks.forEach((deck, index) => {
      const clsLabel = escapeHtml(deck.class);
      const arch = escapeHtml(deck.archetype || deck.title || "Unknown");
      const fmtLabel = escapeHtml(deck.format || "Standard");
      
      // Calculate winrate or score
      let metric = "-";
      if (deck.win_rate) metric = `<strong style="color: #2ec4b6;">${deck.win_rate.toFixed(1)}%</strong>`;
      else if (deck.score) metric = `<span style="font-family: monospace;">${escapeHtml(deck.score)}</span>`;
      
      // Determine source label
      let srcLabel = deck.source_id;
      if (deck.source_id === "hearthstone_decks") srcLabel = "HS-Decks.net";
      else if (deck.source_id === "metastats_decks") srcLabel = "MetaStats.net";
      else if (deck.source_id === "vicious_syndicate_radars") srcLabel = "Vicious Syndicate";
      else if (deck.source_id === "hsreplay_arena_winning_decks") srcLabel = "HSReplay Arena";

      const hasCode = !!deck.deck_code;
      const codeId = `db-code-input-${index}`;
      const btnId = `db-code-btn-${index}`;

      html += `
        <tr>
          <td><strong style="color: #ff9f1c;">${clsLabel}</strong></td>
          <td>
            <div><strong>${arch}</strong></div>
            ${deck.title && deck.title !== arch ? `<div class="muted" style="font-size: 0.8rem; margin-top: 0.2rem;">${escapeHtml(deck.title)}</div>` : ""}
            ${deck.url ? `<div style="margin-top: 0.3rem;"><a href="${escapeHtml(deck.url)}" target="_blank" style="font-size: 0.8rem; color: #2ec4b6;">Перейти к источнику ↗</a></div>` : ""}
          </td>
          <td><span class="badge ok" style="font-size: 0.75rem;">${fmtLabel}</span></td>
          <td>${metric}</td>
          <td><span class="muted" style="font-size: 0.85rem;">${srcLabel}</span></td>
          <td>
            ${hasCode ? `
              <div style="display: flex; gap: 6px; align-items: center;">
                <input type="text" id="${codeId}" value="${escapeHtml(deck.deck_code)}" readonly style="background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.3rem 0.5rem; font-size: 0.75rem; border-radius: 4px; width: 150px; font-family: monospace;" onclick="this.select();" />
                <button id="${btnId}" class="source-btn" style="padding: 0.3rem 0.6rem; border-radius: 4px; font-size: 0.75rem; background: #2ec4b6; color: white;">Копировать</button>
              </div>
            ` : `<span class="muted">-</span>`}
          </td>
        </tr>
      `;
    });

    html += `</tbody></table></div>`;
    resultsDiv.innerHTML = html;

    // Attach click events to copy buttons
    data.decks.forEach((deck, index) => {
      if (deck.deck_code) {
        const btn = document.getElementById(`db-code-btn-${index}`);
        if (btn) {
          btn.onclick = () => {
            navigator.clipboard.writeText(deck.deck_code);
            btn.textContent = "Скопировано!";
            btn.style.background = "#ff9f1c";
            btn.style.color = "#000";
            setTimeout(() => {
              btn.textContent = "Копировать";
              btn.style.background = "#2ec4b6";
              btn.style.color = "white";
            }, 1500);
          };
        }
      }
    });

  } catch (err) {
    resultsDiv.innerHTML = `<p class="muted" style="color: #ff3b30;">Ошибка при выполнении поиска: ${escapeHtml(err.message)}</p>`;
  }
}

async function selectArchetypeDb(btn) {
  document.querySelectorAll(".source-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  $("#placeholder").classList.add("hidden");
  const detail = $("#detail");
  detail.classList.remove("hidden");
  detail.innerHTML = `
    <h2>Архетипы HSReplay</h2>
    <p class="meta-line">Локальная SQLite-база: Standard Legend, summary, mulligan guide, matchups, popular decks.</p>
    <div class="block archetype-controls">
      <label>
        <span>Класс</span>
        <select id="archetype-class-filter">
          <option value="">Все классы</option>
          <option value="DEATHKNIGHT">Рыцарь смерти</option>
          <option value="DEMONHUNTER">Охотник на демонов</option>
          <option value="DRUID">Друид</option>
          <option value="HUNTER">Охотник</option>
          <option value="MAGE">Маг</option>
          <option value="PALADIN">Паладин</option>
          <option value="PRIEST">Жрец</option>
          <option value="ROGUE">Разбойник</option>
          <option value="SHAMAN">Шаман</option>
          <option value="WARLOCK">Чернокнижник</option>
          <option value="WARRIOR">Воин</option>
        </select>
      </label>
      <label>
        <span>Поиск</span>
        <input id="archetype-query" type="text" placeholder="Herald, Rogue или id" />
      </label>
      <button id="archetype-search-btn" class="mini-action">Обновить</button>
    </div>
    <div id="archetype-db-results" class="block"><p>Загрузка...</p></div>
  `;
  $("#archetype-search-btn").onclick = loadArchetypeDbList;
  $("#archetype-query").onkeydown = (e) => {
    if (e.key === "Enter") loadArchetypeDbList();
  };
  $("#archetype-class-filter").onchange = loadArchetypeDbList;
  await loadArchetypeDbList();
}

async function selectBgMinionsDb(btn) {
  document.querySelectorAll(".source-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  $("#placeholder").classList.add("hidden");
  const detail = $("#detail");
  detail.classList.remove("hidden");
  detail.innerHTML = `
    <h2>BG существа HSReplay</h2>
    <p class="meta-line">Локальная SQLite-база: latest snapshots, combat-round статистика и история для графиков.</p>
    <div class="bg-db-grid">
      <section class="block bg-shot-block">
        <div class="archetype-db-head">
          <div>
            <h3>Compositions screenshot</h3>
            <p class="muted" id="bg-shot-meta">Загрузка скриншота Firecrawl...</p>
          </div>
          <a href="/api/bg/compositions/screenshot/latest/image" target="_blank" rel="noopener">PNG</a>
        </div>
        <img id="bg-compositions-shot" class="bg-compositions-shot" alt="Battlegrounds compositions table" />
      </section>
      <section class="block">
        <h3>Фильтры существ</h3>
        <div class="archetype-controls bg-minion-controls">
          <label>
            <span>Таверна</span>
            <select id="bg-minion-tier-filter">
              <option value="">Все уровни</option>
              <option value="1">Таверна 1</option>
              <option value="2">Таверна 2</option>
              <option value="3">Таверна 3</option>
              <option value="4">Таверна 4</option>
              <option value="5">Таверна 5</option>
              <option value="6">Таверна 6</option>
              <option value="7">Таверна 7</option>
            </select>
          </label>
          <label>
            <span>Поиск</span>
            <input id="bg-minion-query" type="text" placeholder="Scrap Scraper, Картежница или dbfId" />
          </label>
          <button id="bg-minion-search-btn" class="mini-action">Обновить</button>
        </div>
      </section>
    </div>
    <div id="bg-minions-results" class="block"><p>Загрузка...</p></div>
  `;
  $("#bg-minion-search-btn").onclick = loadBgMinionsList;
  $("#bg-minion-query").onkeydown = (e) => {
    if (e.key === "Enter") loadBgMinionsList();
  };
  $("#bg-minion-tier-filter").onchange = loadBgMinionsList;
  await Promise.all([loadBgCompositionsScreenshot(), loadBgMinionsList()]);
}

async function loadBgCompositionsScreenshot() {
  const img = document.getElementById("bg-compositions-shot");
  const meta = document.getElementById("bg-shot-meta");
  if (!img || !meta) return;
  try {
    const res = await fetch("/api/bg/compositions/screenshot/latest");
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Screenshot API error");
    meta.textContent = `Firecrawl · ${formatDateRu(data.captured_at)} · ${Math.round((data.image_bytes || 0) / 1024)} KB`;
    img.src = `/api/bg/compositions/screenshot/latest/image?t=${encodeURIComponent(data.captured_at || Date.now())}`;
  } catch (err) {
    meta.textContent = `Не удалось загрузить screenshot: ${err.message}`;
    img.removeAttribute("src");
  }
}

async function loadBgMinionsList() {
  const box = $("#bg-minions-results");
  if (!box) return;
  box.innerHTML = "<p>Загрузка существ...</p>";
  const tier = $("#bg-minion-tier-filter")?.value || "";
  const q = $("#bg-minion-query")?.value.trim() || "";
  let url = "/api/db/bg/minions?limit=265";
  if (tier) url += `&tavern_tier=${encodeURIComponent(tier)}`;
  if (q) url += `&q=${encodeURIComponent(q)}`;
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "API error");
    const latest = data.latest_run || {};
    let html = `
      <div class="archetype-db-head">
        <div>
          <h3>Существа (${data.total || 0})</h3>
          <p class="muted">Последний run: ${escapeHtml(latest.state || "нет данных")} · ${escapeHtml(formatDateRu(latest.completed_at || latest.started_at))} · ok ${escapeHtml(String(latest.minions_ok ?? 0))}/${escapeHtml(String(latest.minions_total ?? 0))}</p>
        </div>
        <a href="/api/db/bg/minions?limit=265" target="_blank" rel="noopener">JSON</a>
      </div>
    `;
    if (!data.minions?.length) {
      box.innerHTML = html + `<p class="muted">Существа не найдены.</p>`;
      return;
    }
    html += `<div class="table-scroll"><table class="simple bg-minions-table"><thead><tr>
      <th>Существо</th><th>Таверна</th><th>Impact</th><th>Combat WR</th><th>Popularity</th><th>Avg with</th><th>Игры</th><th>Детали</th>
    </tr></thead><tbody>`;
    for (const m of data.minions) {
      html += `<tr>
        <td>
          <button class="link-button" data-bg-minion-id="${escapeHtml(String(m.dbf_id))}">${escapeHtml(m.name)}</button>
          <div class="muted">${escapeHtml(m.name_ru || "")} · dbfId ${escapeHtml(String(m.dbf_id))}</div>
        </td>
        <td>${escapeHtml(String(m.tavern_tier ?? ""))}</td>
        <td><strong class="${Number(m.impact || 0) >= 0 ? "metric-good" : "metric-bad"}">${numberCell(m.impact)}</strong></td>
        <td>${numberCell(m.combat_winrate)}%</td>
        <td>${numberCell(m.popularity)}%</td>
        <td>${numberCell(m.avg_placement_with)}</td>
        <td>${escapeHtml(formatInt(m.games_with_minion))}</td>
        <td><button class="mini-action" data-bg-minion-id="${escapeHtml(String(m.dbf_id))}">Открыть</button></td>
      </tr>`;
    }
    html += "</tbody></table></div>";
    box.innerHTML = html;
    box.querySelectorAll("[data-bg-minion-id]").forEach((el) => {
      el.addEventListener("click", () => loadBgMinionDetail(el.dataset.bgMinionId));
    });
  } catch (err) {
    box.innerHTML = `<p class="muted" style="color: var(--err);">Ошибка загрузки: ${escapeHtml(err.message)}</p>`;
  }
}

async function loadBgMinionDetail(dbfId) {
  const box = $("#bg-minions-results");
  if (!box) return;
  box.innerHTML = "<p>Загрузка существа...</p>";
  try {
    const [detailRes, historyRes] = await Promise.all([
      fetch(`/api/db/bg/minions/${encodeURIComponent(dbfId)}`),
      fetch(`/api/db/bg/minions/${encodeURIComponent(dbfId)}/history`),
    ]);
    const detail = await detailRes.json();
    const history = await historyRes.json();
    if (!detailRes.ok) throw new Error(detail.detail || "Detail API error");
    const raw = detail.raw || {};
    const rounds = detail.rounds || [];
    const chartApiUrl = `/api/db/bg/minions/${encodeURIComponent(detail.dbf_id)}/history`;
    let html = `
      <button class="mini-action" id="bg-minion-back-btn">Назад к списку</button>
      <div class="archetype-title">
        <div>
          <h3>${escapeHtml(detail.name || raw.name || "Unknown")}</h3>
          <p class="muted">${escapeHtml(detail.name_ru || "")} · dbfId ${escapeHtml(String(detail.dbf_id))} · ${escapeHtml(formatDateRu(detail.fetched_at))}</p>
        </div>
        <a href="/api/db/bg/minions/${escapeHtml(String(detail.dbf_id))}" target="_blank" rel="noopener">JSON</a>
      </div>
      <div class="archetype-kpis">
        <span>Impact <b>${numberCell(detail.impact)}</b></span>
        <span>Combat WR <b>${numberCell(detail.combat_winrate)}%</b></span>
        <span>Popularity <b>${numberCell(detail.popularity)}%</b></span>
        <span>Games <b>${escapeHtml(formatInt(detail.games_with_minion))}</b></span>
      </div>
      <section class="block bg-api-data-block">
        <h3>Данные для внешнего графика</h3>
        <p class="muted">UI показывает только таблицы. Для красивого графика на другом сайте берите готовые JSON endpoints.</p>
        <div class="bg-api-links">
          <a href="/api/db/bg/minions/${escapeHtml(String(detail.dbf_id))}" target="_blank" rel="noopener">Latest detail JSON</a>
          <a href="${escapeHtml(chartApiUrl)}" target="_blank" rel="noopener">History + chart_series JSON</a>
        </div>
        ${renderTableFromObjects(
          (history.history || []).map((row) => ({
            Дата: formatDateRu(row.fetched_at),
            Impact: numberCell(row.impact),
            "Combat WR": `${numberCell(row.combat_winrate)}%`,
            Popularity: `${numberCell(row.popularity)}%`,
            "Avg with": numberCell(row.avg_placement_with),
            Игры: formatInt(row.games_with_minion),
          })),
          ["Дата", "Impact", "Combat WR", "Popularity", "Avg with", "Игры"],
          { limit: null }
        )}
      </section>
      <section class="block">
        <h3>Round stats (${rounds.length})</h3>
        ${renderTableFromObjects(
          rounds.map((r) => ({
            Раунд: r.combat_round,
            Impact: numberCell(r.impact),
            "Combat WR": `${numberCell(r.combat_winrate)}%`,
            "Avg with": numberCell(r.avg_placement_with),
            "Avg without": numberCell(r.avg_placement_without),
            "Games with": formatInt(r.games_with_minion),
            "Games without": formatInt(r.games_without_minion),
          })),
          ["Раунд", "Impact", "Combat WR", "Avg with", "Avg without", "Games with", "Games without"],
          { limit: null }
        )}
      </section>
    `;
    box.innerHTML = html;
    $("#bg-minion-back-btn").onclick = loadBgMinionsList;
  } catch (err) {
    box.innerHTML = `<button class="mini-action" onclick="loadBgMinionsList()">Назад</button><p class="muted" style="color: var(--err);">Ошибка: ${escapeHtml(err.message)}</p>`;
  }
}

async function selectBgHeroesDb(btn) {
  document.querySelectorAll(".source-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  $("#placeholder").classList.add("hidden");
  const detail = $("#detail");
  detail.classList.remove("hidden");
  detail.dataset.bgHeroMode = "solo";
  detail.innerHTML = `
    <h2>BG герои HSReplay</h2>
    <p class="meta-line">TOP 50% MMR · текущий патч Battlegrounds. Solo хранит подробные графики по герою, Duos показывает отдельный тир-лист.</p>
    <div class="block bg-hero-toolbar">
      <div class="bg-hero-tabs" role="tablist" aria-label="Battlegrounds hero mode">
        <button class="mini-action active" data-bg-hero-mode="solo">Solo</button>
        <button class="mini-action" data-bg-hero-mode="duos">Duos</button>
      </div>
      <label>
        <span>Поиск героя</span>
        <input id="bg-hero-query" type="text" placeholder="Teron, Тэрон или dbfId" />
      </label>
      <button id="bg-hero-search-btn" class="mini-action">Обновить</button>
    </div>
    <div id="bg-heroes-results" class="block"><p>Загрузка...</p></div>
  `;
  detail.querySelectorAll("[data-bg-hero-mode]").forEach((el) => {
    el.addEventListener("click", () => {
      detail.dataset.bgHeroMode = el.dataset.bgHeroMode || "solo";
      detail.querySelectorAll("[data-bg-hero-mode]").forEach((b) => b.classList.remove("active"));
      el.classList.add("active");
      loadBgHeroesList();
    });
  });
  $("#bg-hero-search-btn").onclick = loadBgHeroesList;
  $("#bg-hero-query").onkeydown = (e) => {
    if (e.key === "Enter") loadBgHeroesList();
  };
  await loadBgHeroesList();
}

function bgHeroMode() {
  return $("#detail")?.dataset.bgHeroMode || "solo";
}

function bgMetric(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "-";
  return `${escapeHtml(String(value))}${suffix}`;
}

function bgTierBadge(tier) {
  const label = tier || "-";
  return `<span class="bg-tier-badge bg-tier-${escapeHtml(String(label)).toLowerCase()}">${escapeHtml(String(label))}</span>`;
}

function bgHeroPlacement(placementDistribution, limit = 8) {
  if (!Array.isArray(placementDistribution) || !placementDistribution.length) return "-";
  return placementDistribution
    .slice(0, limit)
    .map((value, index) => `${index + 1}: ${value || "-"}`)
    .join(" · ");
}

function bgHeroPlacementCells(placementDistribution) {
  const values = Array.isArray(placementDistribution) ? placementDistribution : [];
  return Array.from({ length: 8 }, (_, index) => `<td class="bg-place-cell">${escapeHtml(values[index] || "-")}</td>`).join("");
}

function bgCompositionName(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  return value.name || (value.composition_id ? `Composition ${value.composition_id}` : "");
}

async function loadBgHeroesList() {
  const box = $("#bg-heroes-results");
  if (!box) return;
  const mode = bgHeroMode();
  box.innerHTML = `<p>Загрузка ${mode === "duos" ? "дуо тир-листа" : "героев"}...</p>`;
  const q = $("#bg-hero-query")?.value.trim() || "";
  let url = `/api/bg/heroes?mode=${encodeURIComponent(mode)}`;
  if (q) url += `&q=${encodeURIComponent(q)}`;
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "API error");
    const heroes = data.heroes || [];
    const topHeroes = heroes.slice(0, 6);
    let html = `
      <div class="archetype-db-head">
        <div>
          <h3>${mode === "duos" ? "Duos tier list" : "Solo heroes"} (${data.count || 0})</h3>
          <p class="muted">Данные: ${escapeHtml(formatDateRu(data.fetched_at))} · ${escapeHtml(data.source?.backend || "cache")} · ${escapeHtml(data.filters?.time_range || "")}</p>
        </div>
        <a href="${escapeHtml(url)}" target="_blank" rel="noopener">JSON</a>
      </div>
    `;
    if (!heroes.length) {
      box.innerHTML = html + `<p class="muted">Герои не найдены. Если это первый запуск, запустите refresh-bg-hero-details.</p>`;
      return;
    }
    html += `<div class="bg-hero-topline">${topHeroes
      .map((h) => `<article>
        ${bgTierBadge(h.tier)}
        <strong>${escapeHtml(h.hero || "Unknown")}</strong>
        <span>${bgMetric(h.avg_placement)} avg · ${bgMetric(h.pick_rate)}</span>
      </article>`)
      .join("")}</div>`;
    html += `<div class="table-scroll"><table class="simple bg-heroes-table"><thead><tr>
      <th>Герой</th><th>Tier</th><th>Avg</th><th>Adj avg</th><th>Pick</th><th>1</th><th>2</th><th>3</th><th>4</th><th>5</th><th>6</th><th>7</th><th>8</th>${mode === "solo" ? "<th>Лучший состав</th><th>Детали</th>" : ""}
    </tr></thead><tbody>`;
    for (const h of heroes) {
      html += `<tr>
        <td>
          <button class="link-button" ${mode === "solo" ? `data-bg-hero-id="${escapeHtml(String(h.dbfId))}"` : ""}>${escapeHtml(h.hero || "Unknown")}</button>
          <div class="muted">dbfId ${escapeHtml(String(h.dbfId || ""))}${h.detail_available === false && mode === "solo" ? " · fallback" : ""}</div>
        </td>
        <td>${bgTierBadge(h.tier)}</td>
        <td><strong>${bgMetric(h.avg_placement)}</strong></td>
        <td>${bgMetric(h.adjusted_avg_placement)}</td>
        <td>${bgMetric(h.pick_rate)}</td>
        ${bgHeroPlacementCells(h.placement_distribution)}
        ${mode === "solo"
          ? `<td>${escapeHtml(bgCompositionName(h.best_composition)) || "<span class='muted'>нет данных</span>"}</td><td><button class="mini-action" data-bg-hero-id="${escapeHtml(String(h.dbfId))}">Открыть</button></td>`
          : ""}
      </tr>`;
    }
    html += "</tbody></table></div>";
    if (mode === "duos") {
      html += `<p class="muted bg-hero-note">В duos режиме сохраняется только тир-лист HSReplay. Подбор лучшего состава и графики героя не запрашиваются.</p>`;
    }
    box.innerHTML = html;
    box.querySelectorAll("[data-bg-hero-id]").forEach((el) => {
      el.addEventListener("click", () => loadBgHeroDetail(el.dataset.bgHeroId));
    });
  } catch (err) {
    box.innerHTML = `<p class="muted" style="color: var(--err);">Ошибка загрузки: ${escapeHtml(err.message)}</p>`;
  }
}

function renderBgMinionChips(cards, limit = 12) {
  const rows = Array.isArray(cards) ? cards.slice(0, limit) : [];
  if (!rows.length) return `<p class="muted">Нет данных.</p>`;
  return `<div class="bg-minion-chip-grid">${rows
    .map((card) => `<span>
      <b>${escapeHtml(card.minion || card.name || `dbfId ${card.minion_dbf_id || card.dbfId || ""}`)}</b>
      <small>${escapeHtml([
        card.tavern_tier ? `T${card.tavern_tier}` : "",
        card.at_least_one ? `${card.at_least_one} games` : "",
        card.premium ? "golden" : "",
      ].filter(Boolean).join(" · "))}</small>
    </span>`)
    .join("")}</div>`;
}

function renderBgLineup(cards) {
  const rows = Array.isArray(cards) ? cards : [];
  if (!rows.length) return `<p class="muted">Lineup не найден.</p>`;
  return `<ol class="bg-lineup">${rows
    .map((card) => `<li>
      <strong>${escapeHtml(card.minion || card.name || `dbfId ${card.minion_dbf_id || ""}`)}</strong>
      <span>${escapeHtml([
        card.premium ? "golden" : "",
        card.attack !== undefined && card.health !== undefined ? `${card.attack}/${card.health}` : "",
        card.taunt ? "taunt" : "",
        card.divine_shield ? "divine shield" : "",
        card.poison ? "poison" : "",
      ].filter(Boolean).join(" · "))}</span>
    </li>`)
    .join("")}</ol>`;
}

async function loadBgHeroDetail(dbfId) {
  const box = $("#bg-heroes-results");
  if (!box) return;
  box.innerHTML = "<p>Загрузка героя...</p>";
  try {
    const res = await fetch(`/api/bg/heroes/${encodeURIComponent(dbfId)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Hero API error");
    const hero = data.hero || {};
    const best = data.best_composition || {};
    const sourceUrl = data.source_url || `https://hsreplay.net/battlegrounds/heroes/${encodeURIComponent(dbfId)}/-`;
    let html = `
      <button class="mini-action" id="bg-hero-back-btn">Назад к тир-листу</button>
      <div class="archetype-title bg-hero-title">
        <div>
          <h3>${escapeHtml(hero.hero || "Unknown hero")} ${bgTierBadge(hero.tier)}</h3>
          <p class="muted">dbfId ${escapeHtml(String(hero.dbfId || dbfId))} · ${escapeHtml(data.filters?.mmr_percentile || "")} · ${escapeHtml(data.filters?.time_range || "")}</p>
        </div>
        <div class="bg-api-links">
          <a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener">HSReplay</a>
          <a href="/api/bg/heroes/${escapeHtml(String(dbfId))}" target="_blank" rel="noopener">JSON</a>
        </div>
      </div>
      <div class="archetype-kpis bg-hero-kpis">
        <span>Avg placement <b>${bgMetric(hero.avg_placement)}</b></span>
        <span>Adjusted avg <b>${bgMetric(hero.adjusted_avg_placement)}</b></span>
        <span>Pick rate <b>${bgMetric(hero.pick_rate)}</b></span>
        <span>Best comp <b>${escapeHtml(bgCompositionName(best) || bgCompositionName(hero.best_composition) || "-")}</b></span>
      </div>
      <section class="block bg-api-data-block">
        <h3>Когда улучшать таверну</h3>
        <p class="muted">Рекомендация берется как самый частый уровень таверны в конце recruit turn у игроков TOP 50% MMR. as_of: ${escapeHtml(formatDateRu(data.as_of?.tavern_up))}</p>
        <div class="table-scroll">
          ${renderTableFromObjects(
            (data.tavern_up_by_turn || []).map((r) => ({
              Ход: r.turn,
              "Таверна": r.recommended_tavern_tier,
              "Доля": `${numberCell(r.pct_at_tier)}%`,
              "Игр": formatInt(r.num_games),
            })),
            ["Ход", "Таверна", "Доля", "Игр"],
            { limit: null }
          )}
        </div>
      </section>
      <section class="block">
        <h3>Когда прожимать силу героя</h3>
        <p class="muted">Сводка по ходам считает weighted average invoked_rate с учетом количества точек данных. as_of: ${escapeHtml(formatDateRu(data.as_of?.hero_power))}</p>
        <div class="table-scroll">
          ${renderTableFromObjects(
            (data.hero_power_by_turn || []).map((r) => ({
              Ход: r.turn,
              "Hero power": `${numberCell(r.invoked_rate)}%`,
              "Точек данных": formatInt(r.total_data_points),
            })),
            ["Ход", "Hero power", "Точек данных"],
            { limit: null }
          )}
        </div>
      </section>
    `;
    if (best && Object.keys(best).length) {
      html += `
        <section class="block bg-best-comp">
          <h3>Лучший состав</h3>
          <div class="bg-comp-summary">
            <span>Состав <b>${escapeHtml(bgCompositionName(best) || "-")}</b></span>
            <span>Avg <b>${bgMetric(best.avg_placement)}</b></span>
            <span>Popularity <b>${bgMetric(best.popularity)}</b></span>
            <span>Games <b>${escapeHtml(formatInt(best.num_games))}</b></span>
          </div>
          <div class="bg-detail-grid">
            <div>
              <h4>Lineup</h4>
              ${renderBgLineup(best.lineup)}
            </div>
            <div>
              <h4>Final form minions</h4>
              ${renderBgMinionChips(best.final_form_minions, 12)}
            </div>
          </div>
        </section>
      `;
    }
    html += `
      <section class="block">
        <h3>Подробная статистика</h3>
        <div class="bg-detail-grid">
          <div>
            <h4>Hero power по таверне</h4>
            <div class="table-scroll">
              ${renderTableFromObjects(
                (data.hero_power || []).map((r) => ({
                  Ход: r.turn,
                  "Таверна": r.tavern_tier,
                  Gold: r.gold,
                  "Median tier": r.end_of_round_median_tavern_tier,
                  "Hero power": `${numberCell(r.invoked_rate)}%`,
                  "Прожато": numberCell(r.times_invoked),
                  "Точек": formatInt(r.total_data_points),
                })),
                ["Ход", "Таверна", "Gold", "Median tier", "Hero power", "Прожато", "Точек"],
                { limit: null }
              )}
            </div>
          </div>
          <div>
            <h4>Таверна по ходам</h4>
            <div class="table-scroll">
              ${renderTableFromObjects(
                (data.tavern_up || []).map((r) => ({
                  Ход: r.turn,
                  "Таверна": r.tavern_tier,
                  "Доля": `${numberCell(r.pct_at_tier)}%`,
                  "Occurrences": formatInt(r.occurrences),
                  "Игр": formatInt(r.num_games),
                })),
                ["Ход", "Таверна", "Доля", "Occurrences", "Игр"],
                { limit: null }
              )}
            </div>
          </div>
        </div>
      </section>
      <section class="block">
        <h3>Топ составы героя</h3>
        <div class="table-scroll">
          ${renderTableFromObjects(
            (data.compositions || []).slice(0, 20).map((c) => ({
              Состав: c.name || `Composition ${c.composition_id}`,
              Avg: numberCell(c.avg_placement),
              Popularity: c.popularity || "",
              "Top 4 pop": c.popularity_top_4 || "",
              Games: formatInt(c.num_games),
            })),
            ["Состав", "Avg", "Popularity", "Top 4 pop", "Games"],
            { limit: null }
          )}
        </div>
      </section>
    `;
    box.innerHTML = html;
    $("#bg-hero-back-btn").onclick = loadBgHeroesList;
  } catch (err) {
    box.innerHTML = `<button class="mini-action" onclick="loadBgHeroesList()">Назад</button><p class="muted" style="color: var(--err);">Ошибка: ${escapeHtml(err.message)}</p>`;
  }
}

function numberCell(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  if (Number.isNaN(n)) return escapeHtml(String(value));
  return n.toFixed(digits);
}

function formatInt(value) {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return new Intl.NumberFormat("ru-RU").format(n);
}

function pctCell(value) {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  if (Number.isNaN(n)) return escapeHtml(String(value));
  return `${n.toFixed(2)}%`;
}

async function loadArchetypeDbList() {
  const box = $("#archetype-db-results");
  if (!box) return;
  box.innerHTML = "<p>Загрузка...</p>";
  const cls = $("#archetype-class-filter")?.value || "";
  const q = $("#archetype-query")?.value.trim() || "";
  let url = "/api/db/archetypes?limit=200";
  if (cls) url += `&class_name=${encodeURIComponent(cls)}`;
  if (q) url += `&q=${encodeURIComponent(q)}`;
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "API error");
    const latest = data.latest_run;
    let html = `
      <div class="archetype-db-head">
        <div>
          <h3>Архетипы (${data.total || 0})</h3>
          <p class="muted">Последний run: ${escapeHtml(latest?.state || "нет данных")} · ${escapeHtml(formatDateRu(latest?.completed_at || latest?.started_at))}</p>
        </div>
        <a href="/docs#/default/db_archetypes_api_db_archetypes_get" target="_blank" rel="noopener">API</a>
      </div>
    `;
    if (!data.archetypes?.length) {
      box.innerHTML = html + `<p class="muted">В базе пока нет архетипов. Запустите refresh-hsreplay-archetypes.</p>`;
      return;
    }
    html += `<table class="simple archetype-table"><thead><tr>
      <th>Архетип</th><th>Класс</th><th>Winrate</th><th>Meta</th><th>В классе</th><th>Игры</th><th>Данные</th>
    </tr></thead><tbody>`;
    for (const a of data.archetypes) {
      html += `<tr>
        <td><button class="link-button" data-archetype-id="${escapeHtml(String(a.archetype_id))}">${escapeHtml(a.name)}</button><div class="muted">#${escapeHtml(String(a.archetype_id))}</div></td>
        <td>${escapeHtml(a.class_name || a.player_class || "")}</td>
        <td><strong>${pctCell(a.win_rate)}</strong></td>
        <td>${pctCell(a.pct_of_total)}</td>
        <td>${pctCell(a.pct_of_class)}</td>
        <td>${escapeHtml(String(a.total_games ?? ""))}</td>
        <td><span class="muted">${escapeHtml(formatDateRu(a.fetched_at))}</span></td>
      </tr>`;
    }
    html += "</tbody></table>";
    box.innerHTML = html;
    box.querySelectorAll("[data-archetype-id]").forEach((el) => {
      el.addEventListener("click", () => loadArchetypeDetail(el.dataset.archetypeId));
    });
  } catch (err) {
    box.innerHTML = `<p class="muted" style="color: var(--err);">Ошибка загрузки: ${escapeHtml(err.message)}</p>`;
  }
}

async function loadArchetypeDetail(archetypeId) {
  const box = $("#archetype-db-results");
  if (!box) return;
  box.innerHTML = "<p>Загрузка архетипа...</p>";
  try {
    const res = await fetch(`/api/db/archetypes/${encodeURIComponent(archetypeId)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "API error");
    const s = data.snapshot;
    const best = [...(data.matchups || [])].filter((m) => (m.total_games || 0) >= 100).sort((a, b) => (b.win_rate || 0) - (a.win_rate || 0)).slice(0, 6);
    const worst = [...(data.matchups || [])].filter((m) => (m.total_games || 0) >= 100).sort((a, b) => (a.win_rate || 0) - (b.win_rate || 0)).slice(0, 6);
    let html = `
      <button class="mini-action" id="archetype-back-btn">Назад к списку</button>
      <div class="archetype-title">
        <div>
          <h3>${escapeHtml(s.name)}</h3>
          <p class="muted">${escapeHtml(s.class_name || s.player_class || "")} · #${escapeHtml(String(s.archetype_id))} · ${escapeHtml(s.rank_range)} · ${escapeHtml(s.game_type)}</p>
        </div>
        ${s.url ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">HSReplay</a>` : ""}
      </div>
      <div class="archetype-kpis">
        <span>Winrate <b>${pctCell(s.win_rate)}</b></span>
        <span>Игры <b>${escapeHtml(String(s.total_games ?? ""))}</b></span>
        <span>Meta <b>${pctCell(s.pct_of_total)}</b></span>
        <span>В классе <b>${pctCell(s.pct_of_class)}</b></span>
      </div>
      <div class="archetype-columns">
        <section>
          <h4>Mulligan guide (${data.mulligan.length})</h4>
          ${renderTableFromObjects(
            data.mulligan.map((c) => ({
              "#": c.hsreplay_rank,
              Карта: c.card_name,
              Keep: pctCell(c.keep_percentage),
              "Opening WR": pctCell(c.opening_hand_winrate),
              "Drawn WR": pctCell(c.winrate_when_drawn),
              "Played WR": pctCell(c.winrate_when_played),
              "Показана": c.times_presented_in_initial_cards ?? "",
            })),
            ["#", "Карта", "Keep", "Opening WR", "Drawn WR", "Played WR", "Показана"],
            { limit: null }
          )}
        </section>
        <section>
          <h4>Лучшие матчапы от 100 игр</h4>
          ${renderTableFromObjects(
            best.map((m) => ({ Оппонент: m.opponent_name, Игры: m.total_games, Winrate: pctCell(m.win_rate) })),
            ["Оппонент", "Игры", "Winrate"],
            { limit: null }
          )}
          <h4>Худшие матчапы от 100 игр</h4>
          ${renderTableFromObjects(
            worst.map((m) => ({ Оппонент: m.opponent_name, Игры: m.total_games, Winrate: pctCell(m.win_rate) })),
            ["Оппонент", "Игры", "Winrate"],
            { limit: null }
          )}
        </section>
      </div>
      <section>
        <h4>Сборки (${data.decks.length})</h4>
        <div class="deck-grid">
    `;
    for (const deck of data.decks.slice(0, 24)) {
      html += `<article class="deck-tile">
        <div>
          <strong>${escapeHtml(deck.deck_id)}</strong>
          <p class="muted">${escapeHtml(String(deck.total_games ?? ""))} игр · ${pctCell(deck.win_rate)} · ${escapeHtml(String(deck.card_count || 30))} карт</p>
        </div>
        <div class="deck-actions">
          ${deck.url ? `<a href="${escapeHtml(deck.url)}" target="_blank" rel="noopener">HSReplay</a>` : ""}
          <button class="mini-action" data-deck-cards="${escapeHtml(String(deck.id))}">Карты</button>
        </div>
        <div class="deck-cards" id="deck-cards-${escapeHtml(String(deck.id))}"></div>
      </article>`;
    }
    html += `</div></section>`;
    box.innerHTML = html;
    $("#archetype-back-btn").onclick = loadArchetypeDbList;
    box.querySelectorAll("[data-deck-cards]").forEach((el) => {
      el.addEventListener("click", () => loadArchetypeDeckCards(s.archetype_id, el.dataset.deckCards));
    });
  } catch (err) {
    box.innerHTML = `<button class="mini-action" onclick="loadArchetypeDbList()">Назад</button><p class="muted" style="color: var(--err);">Ошибка: ${escapeHtml(err.message)}</p>`;
  }
}

async function loadArchetypeDeckCards(archetypeId, deckDbId) {
  const target = document.getElementById(`deck-cards-${deckDbId}`);
  if (!target) return;
  if (target.dataset.loaded === "true") {
    target.innerHTML = "";
    target.dataset.loaded = "false";
    return;
  }
  target.innerHTML = "<p class='muted'>Загрузка карт...</p>";
  const res = await fetch(`/api/db/archetypes/${encodeURIComponent(archetypeId)}/decks?include_cards=true&limit=100`);
  const data = await res.json();
  const deck = (data.decks || []).find((d) => String(d.id) === String(deckDbId));
  if (!deck) {
    target.innerHTML = "<p class='muted'>Карты не найдены.</p>";
    return;
  }
  const main = (deck.cards || []).filter((c) => !c.sideboard);
  const side = (deck.cards || []).filter((c) => c.sideboard);
  target.dataset.loaded = "true";
  target.innerHTML = `
    <ul class="cards-list compact-cards">${main.map((c) => `<li><strong>${escapeHtml(String(c.count || 1))}x</strong> ${escapeHtml(c.card_name)}</li>`).join("")}</ul>
    ${side.length ? `<p class="muted">Sideboard</p><ul class="cards-list compact-cards">${side.map((c) => `<li><strong>${escapeHtml(String(c.count || 1))}x</strong> ${escapeHtml(c.card_name)}</li>`).join("")}</ul>` : ""}
  `;
}

function initMetaScatterChart(strategies) {
  const canvas = document.getElementById("meta-scatter-canvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const hoverInfo = document.getElementById("meta-hover-info");

  // Parse strategies to coordinate points
  const parsedPoints = strategies.map(s => {
    const name = s.Archetype || s.strategy || s.name || "";
    const winrateStr = s["Winrate↓"] || s.Winrate || s.winrate || "0";
    const popularityStr = s.Popularity || s.popularity || "0%";
    
    const winrate = parseFloat(winrateStr) || 0;
    
    let popularity = 0;
    const popMatch = popularityStr.match(/^([\d.,]+)/);
    if (popMatch) {
      popularity = parseFloat(popMatch[1].replace(',', '.')) || 0;
    }
    
    let hsClass = "Neutral";
    const nameLower = name.toLowerCase();
    if (nameLower.includes("dh") || nameLower.includes("demon hunter") || nameLower.includes("demonhunter")) {
      hsClass = "Demon Hunter";
    } else if (nameLower.includes("dk") || nameLower.includes("death knight") || nameLower.includes("deathknight")) {
      hsClass = "Death Knight";
    } else if (nameLower.includes("druid")) {
      hsClass = "Druid";
    } else if (nameLower.includes("hunter")) {
      hsClass = "Hunter";
    } else if (nameLower.includes("mage")) {
      hsClass = "Mage";
    } else if (nameLower.includes("paladin") || nameLower.includes("turnadin")) {
      hsClass = "Paladin";
    } else if (nameLower.includes("priest")) {
      hsClass = "Priest";
    } else if (nameLower.includes("rogue")) {
      hsClass = "Rogue";
    } else if (nameLower.includes("shaman")) {
      hsClass = "Shaman";
    } else if (nameLower.includes("warlock") || nameLower.includes("egglock") || nameLower.includes("rafaamlock")) {
      hsClass = "Warlock";
    } else if (nameLower.includes("warrior")) {
      hsClass = "Warrior";
    }
    
    return {
      name,
      winrate,
      popularity,
      popularityStr,
      turns: s.Turns || s.turns || "",
      duration: s.Duration || s.duration || "",
      climbingSpeed: s["Climbing Speed"] || s.climbing_speed || "",
      hsClass,
      raw: s
    };
  }).filter(p => p.winrate > 0 && p.name);

  // Constants for coloring
  const CLASS_COLORS = {
    "Death Knight": "#008f7d",
    "Demon Hunter": "#a330c9",
    "Druid": "#ff7d0a",
    "Hunter": "#abd473",
    "Mage": "#40c7eb",
    "Paladin": "#f58cba",
    "Priest": "#ffffff",
    "Rogue": "#fff569",
    "Shaman": "#0070de",
    "Warlock": "#8787ed",
    "Warrior": "#c79c6e",
    "Neutral": "#999999"
  };

  // Dimensions
  const width = canvas.width;
  const height = canvas.height;
  const paddingLeft = 60;
  const paddingRight = 40;
  const paddingTop = 30;
  const paddingBottom = 50;

  const chartWidth = width - paddingLeft - paddingRight;
  const chartHeight = height - paddingTop - paddingBottom;

  // Ranges
  const xMin = 35;
  const xMax = 65;
  const yMin = 0;
  const maxPop = Math.max(...parsedPoints.map(p => p.popularity), 5);
  const yMax = Math.ceil(maxPop / 5) * 5;

  // Map to canvas
  function mapX(wr) {
    return paddingLeft + ((wr - xMin) / (xMax - xMin)) * chartWidth;
  }
  function mapY(pop) {
    return height - paddingBottom - ((pop - yMin) / (yMax - yMin)) * chartHeight;
  }

  let hoveredPoint = null;

  function draw() {
    // Clear
    ctx.fillStyle = "#1e1e24";
    ctx.fillRect(0, 0, width, height);

    // Draw Gridlines & Axes
    ctx.strokeStyle = "#2d2d38";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#a0a0b0";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";

    // Horizontal Gridlines (Y-axis: Popularity)
    const yStep = yMax <= 10 ? 2 : 5;
    for (let pop = yMin; pop <= yMax; pop += yStep) {
      const y = mapY(pop);
      // Line
      ctx.beginPath();
      ctx.moveTo(paddingLeft, y);
      ctx.lineTo(width - paddingRight, y);
      ctx.stroke();
      // Text
      ctx.fillText(pop + "%", paddingLeft - 8, y);
    }

    // Vertical Gridlines (X-axis: Winrate)
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let wr = xMin; wr <= xMax; wr += 5) {
      const x = mapX(wr);
      // Line
      ctx.beginPath();
      ctx.moveTo(x, paddingTop);
      ctx.lineTo(x, height - paddingBottom);
      ctx.stroke();
      // Text
      ctx.fillText(wr + "%", x, height - paddingBottom + 8);
    }

    // Draw solid axes
    ctx.strokeStyle = "#4e4e5a";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(paddingLeft, paddingTop);
    ctx.lineTo(paddingLeft, height - paddingBottom);
    ctx.lineTo(width - paddingRight, height - paddingBottom);
    ctx.stroke();

    // Axis titles
    ctx.fillStyle = "#d0d0e0";
    ctx.font = "bold 12px sans-serif";
    ctx.textAlign = "center";
    // X-axis label
    ctx.fillText("Винрейт (Winrate)", paddingLeft + chartWidth / 2, height - paddingBottom + 28);
    // Y-axis label (vertical)
    ctx.save();
    ctx.translate(15, paddingTop + chartHeight / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("Популярность (Popularity)", 0, 0);
    ctx.restore();

    // Draw non-hovered points first (so hovered remains on top)
    parsedPoints.forEach(p => {
      if (p === hoveredPoint) return;
      drawPoint(p, false);
    });

    // Draw hovered point
    if (hoveredPoint) {
      drawPoint(hoveredPoint, true);
    }
  }

  function drawPoint(p, isHovered) {
    const cx = mapX(p.winrate);
    const cy = mapY(p.popularity);
    const color = CLASS_COLORS[p.hsClass] || CLASS_COLORS["Neutral"];

    // Draw Outer Highlight if hovered
    if (isHovered) {
      ctx.beginPath();
      ctx.arc(cx, cy, 10, 0, 2 * Math.PI);
      ctx.fillStyle = "rgba(255, 255, 255, 0.25)";
      ctx.fill();
    }

    // Draw point circle
    ctx.beginPath();
    ctx.arc(cx, cy, isHovered ? 7 : 5, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = isHovered ? "#fff" : "rgba(0, 0, 0, 0.5)";
    ctx.lineWidth = isHovered ? 2 : 1;
    ctx.stroke();

    // Draw Text Label
    ctx.font = isHovered ? "bold 12px sans-serif" : "10px sans-serif";
    ctx.fillStyle = isHovered ? "#fff" : "#cccccc";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";

    // Draw label with nice shadow/glow
    ctx.shadowColor = "rgba(0, 0, 0, 0.95)";
    ctx.shadowBlur = isHovered ? 6 : 4;
    ctx.shadowOffsetX = 1;
    ctx.shadowOffsetY = 1;

    ctx.fillText(p.name, cx + 8, cy);

    // Reset shadow
    ctx.shadowColor = "transparent";
    ctx.shadowBlur = 0;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;
  }

  // Hover detection
  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = (e.clientX - rect.left) * (width / rect.width);
    const mouseY = (e.clientY - rect.top) * (height / rect.height);

    let found = null;
    let minDistance = 15;

    for (const p of parsedPoints) {
      const cx = mapX(p.winrate);
      const cy = mapY(p.popularity);
      const dist = Math.hypot(mouseX - cx, mouseY - cy);
      if (dist < minDistance) {
        minDistance = dist;
        found = p;
      }
    }

    if (found !== hoveredPoint) {
      hoveredPoint = found;
      canvas.style.cursor = hoveredPoint ? "pointer" : "default";
      
      if (hoveredPoint) {
        hoverInfo.innerHTML = `
          <span style="color: ${CLASS_COLORS[hoveredPoint.hsClass]}; font-weight: bold;">${hoveredPoint.name} (${hoveredPoint.hsClass})</span> · 
          Винрейт: <strong style="color: #4cd137">${hoveredPoint.winrate}%</strong> · 
          Популярность: <strong style="color: #00a8ff">${hoveredPoint.popularityStr}</strong>
          ${hoveredPoint.turns ? ` · Ходов: <strong>${hoveredPoint.turns}</strong>` : ""}
          ${hoveredPoint.climbingSpeed ? ` · Скорость: <strong>${hoveredPoint.climbingSpeed}</strong>` : ""}
        `;
      } else {
        hoverInfo.textContent = "Наведите на точку, чтобы увидеть детали";
      }
      draw();
    }
  });

  canvas.addEventListener("mouseleave", () => {
    if (hoveredPoint) {
      hoveredPoint = null;
      hoverInfo.textContent = "Наведите на точку, чтобы увидеть детали";
      canvas.style.cursor = "default";
      draw();
    }
  });

  // Initial draw
  draw();
}
