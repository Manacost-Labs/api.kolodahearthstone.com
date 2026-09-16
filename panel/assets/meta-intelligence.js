"use strict";

(() => {
  const state = {
    epoch: 0, controllers: new Set(), ready: false, view: "archetypes", format: "standard",
    scope: null, health: null, budget: null, rows: [], selected: null, detail: null,
    detailTab: "decks", selectedDecks: new Set(), deckSort: "games", minDeckGames: 0,
    signalState: "", signalKind: "", requestSequence: 0,
  };
  const ranks = {all:"Все ранги",legend:"Legend",diamond:"Diamond",diamond_4to1:"Diamond 4–1",diamond_to_legend:"Diamond → Legend",top_legend:"Top Legend",top_5k:"Top 5K",top_500:"Top 500",top_100:"Top 100",unknown:"Ранг не указан"};
  const periods = {past_day:"24 часа",past_3_days:"3 дня",past_week:"7 дней",past_2_weeks:"14 дней",most_wanted:"Текущий сезон",unknown:"Период не указан"};
  const labels = {fresh:"Свежие",stale:"Устарели",cached:"Последние доступные",missing:"Нет данных",observing:"Наблюдение",early:"Ранний сигнал",confirmed:"Подтверждён",rejected:"Не подтвердился"};
  const signalKinds = {popularity_rise:"Рост популярности",popularity_drop:"Снижение популярности",winrate_shift:"Изменение винрейта",matchup_shift:"Изменение матчапа",deck_variant_detected:"Новая сборка",streamer_burst:"Интерес стримеров"};
  const periodLabel = (value) => periods[value] || (String(value).startsWith("patch_") ? `Патч ${String(value).slice(6)}` : value);

  const root = () => document.getElementById("meta-lab-root");
  const inside = (selector) => root()?.querySelector(selector);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const number = (value, digits = 0) => value == null ? "—" : Number(value).toLocaleString("ru-RU", {maximumFractionDigits:digits, minimumFractionDigits:digits});
  const percent = (value) => value == null ? "—" : `${number(value, 1)}%`;
  const signed = (value) => value == null ? "—" : `${value > 0 ? "+" : ""}${number(value, 1)}`;
  const date = (value) => value ? new Date(value).toLocaleString("ru-RU", {day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"}) : "—";
  const badge = (value) => `<span class="meta-state meta-state--${escape(value)}">${escape(labels[value] || value)}</span>`;
  const empty = (title, text) => `<div class="meta-empty"><strong>${escape(title)}</strong><span>${escape(text)}</span></div>`;

  function abortRequests() {
    state.epoch += 1;
    state.controllers.forEach((controller) => controller.abort());
    state.controllers.clear();
  }

  function showError(error) {
    if (error?.name === "AbortError" || !root()) return;
    const box = inside("#meta-error");
    box.textContent = error?.message || "Не удалось загрузить данные.";
    box.hidden = false;
  }

  function gatewayRequest(path) {
    const parsed = new URL(path, "https://meta-lab.internal");
    const pathname = parsed.pathname;
    let route = "";
    const parameters = Object.fromEntries(parsed.searchParams.entries());
    let match;
    if (pathname === "/health" || pathname === "/budget" || pathname === "/archetypes" || pathname === "/cards" || pathname === "/signals") {
      route = pathname.slice(1);
    } else if ((match = pathname.match(/^\/archetypes\/([^/]+)\/(timeline|compare)$/))) {
      route = match[2];
      parameters.archetype = decodeURIComponent(match[1]);
    } else if ((match = pathname.match(/^\/archetypes\/([^/]+)$/))) {
      route = "archetype";
      parameters.archetype = decodeURIComponent(match[1]);
    } else if ((match = pathname.match(/^\/signals\/([^/]+)$/))) {
      route = "signal";
      parameters.signal_id = decodeURIComponent(match[1]);
    } else if ((match = pathname.match(/^\/snapshots\/([^/]+)$/))) {
      route = "snapshot";
      parameters.snapshot_id = decodeURIComponent(match[1]);
    } else {
      throw new Error("Неизвестный запрос Meta Lab.");
    }
    return new URLSearchParams({module:"meta_lab", route, ...parameters});
  }

  async function request(path) {
    const captured = state.epoch;
    const controller = new AbortController();
    state.controllers.add(controller);
    try {
      const response = await fetch(`${root().dataset.endpoint}?${gatewayRequest(path)}`, {
        headers:{Accept:"application/json"},
        cache:"no-store",
        credentials:"same-origin",
        signal:controller.signal,
      });
      if (captured !== state.epoch || !root()) throw new DOMException("Cancelled", "AbortError");
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) throw new Error("Сессия панели не имеет доступа к Meta Lab.");
        if (response.status === 404) throw new Error("Для выбранного среза этих данных пока нет.");
        if (response.status === 503) throw new Error("Meta Lab временно выключен в API.");
        throw new Error(payload?.message || "API не вернул данные. Попробуйте обновить раздел.");
      }
      return payload;
    } finally {
      state.controllers.delete(controller);
    }
  }

  function scopeQuery(extra = {}) { return new URLSearchParams({...state.scope,...extra}).toString(); }

  function selectScope() {
    const value = inside("#meta-scope")?.value;
    const parts = value ? JSON.parse(value) : null;
    state.scope = parts ? {format:state.format,rank:parts[0],period:parts[1],patch:parts[2]} : null;
    state.selected = null;
    state.detail = null;
    state.selectedDecks.clear();
  }

  function renderScopeOptions() {
    const select = inside("#meta-scope");
    const available = (state.health?.scopes || []).filter((item) => item.format === state.format);
    available.sort((a,b) => Number(b.rank === "legend") - Number(a.rank === "legend") || Number(b.period === "past_week") - Number(a.period === "past_week") || String(b.latest || "").localeCompare(String(a.latest || "")));
    const previous = state.scope && JSON.stringify([state.scope.rank,state.scope.period,state.scope.patch]);
    select.innerHTML = available.length ? available.map((item) => {
      const value = JSON.stringify([item.rank,item.period,item.patch]);
      const patch = item.patch === "unknown" ? "патч не указан" : item.patch.replace("patch_", "патч ");
      return `<option value="${escape(value)}">${escape(ranks[item.rank] || item.rank)} · ${escape(periodLabel(item.period))} · ${escape(patch)}</option>`;
    }).join("") : '<option value="">Нет сохранённых срезов</option>';
    if (available.some((item) => JSON.stringify([item.rank,item.period,item.patch]) === previous)) select.value = previous;
    selectScope();
  }

  function renderSummary() {
    const total = (state.health?.scopes || []).reduce((sum,item) => sum + item.snapshots, 0);
    const budgetText = state.budget?.month_complete ? `${number(state.budget.monthly_spent_hsguru)} credits за месяц` : "Расход с начала месяца ещё не подтверждён";
    inside("#meta-summary").innerHTML = `<span><b>${number(total)}</b> снимков</span><span><b>${number(state.health?.scopes?.length || 0)}</b> срезов</span><span>${escape(budgetText)}</span>`;
  }

  async function load() {
    abortRequests();
    state.ready = false;
    inside("#meta-error").hidden = true;
    inside("#meta-content").setAttribute("aria-busy", "true");
    inside("#meta-refresh").disabled = true;
    try {
      [state.health,state.budget] = await Promise.all([request("/health"),request("/budget")]);
      renderScopeOptions();
      renderSummary();
      state.ready = true;
      await renderView();
    } finally {
      if (root()) { inside("#meta-content").setAttribute("aria-busy", "false"); inside("#meta-refresh").disabled = false; }
    }
  }

  function renderTabs() {
    root()?.querySelectorAll("[data-meta-view]").forEach((button) => {
      const active = button.dataset.metaView === state.view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    inside("#meta-filters").hidden = state.view === "health";
    const search = inside("#meta-search");
    search.closest("label").hidden = state.view === "signals";
    search.placeholder = state.view === "cards" ? "Название карты" : "Название архетипа";
  }

  async function renderView() {
    renderTabs();
    inside("#meta-error").hidden = true;
    if (state.view === "health") return renderHealth();
    if (state.view === "signals") return renderSignals();
    if (!state.scope) { inside("#meta-content").innerHTML = empty("История ещё не собрана", "После первого успешного импорта здесь появятся данные."); return; }
    if (state.view === "cards") return renderCards();
    const result = await request(`/archetypes?${scopeQuery({limit:500})}`);
    state.rows = result.data;
    renderArchetypes();
  }

  function renderArchetypes() {
    const query = inside("#meta-search").value.toLocaleLowerCase("ru-RU");
    const visible = state.rows.filter((row) => row.archetype.toLocaleLowerCase("ru-RU").includes(query));
    if (!visible.length) { inside("#meta-content").innerHTML = empty("Архетипы не найдены", "Измените поиск или выберите другой срез."); return; }
    inside("#meta-content").innerHTML = `<div class="meta-workspace"><section class="block meta-arch-panel"><div class="meta-section-head"><h3>Архетипы</h3><span class="badge ok">${visible.length}</span></div><div class="meta-arch-list">${visible.map((row) => `<button class="meta-arch ${row.archetype === state.selected ? "active" : ""}" data-meta-arch="${escape(row.archetype)}"><span><b>${escape(row.archetype)}</b><small>${number(row.games)} игр · ${row.decks} сборок</small></span><strong>${percent(row.winrate)}</strong></button>`).join("")}</div></section><section class="block meta-detail" id="meta-detail">${empty("Загрузка", "Получаем подробную статистику архетипа.")}</section></div>`;
    const next = visible.some((row) => row.archetype === state.selected) ? state.selected : visible[0].archetype;
    openArchetype(next).catch(showError);
  }

  async function openArchetype(name) {
    const sequence = ++state.requestSequence;
    state.selected = name;
    state.selectedDecks.clear();
    root()?.querySelectorAll("[data-meta-arch]").forEach((button) => button.classList.toggle("active", button.dataset.metaArch === name));
    const result = await request(`/archetypes/${encodeURIComponent(name)}?${scopeQuery()}`);
    if (sequence !== state.requestSequence || !inside("#meta-detail")) return;
    state.detail = result;
    renderArchetypeDetail();
  }

  function renderArchetypeDetail() {
    const row = state.rows.find((item) => item.archetype === state.selected);
    const unverified = state.detail.evidence.some((item) => item.patch_scope !== "single_patch");
    inside("#meta-detail").innerHTML = `<div class="meta-detail-head"><div><span class="block-eyebrow">${escape(ranks[state.scope.rank] || state.scope.rank)} · ${escape(periodLabel(state.scope.period))}</span><h3>${escape(state.detail.archetype)}</h3></div><div>${badge(row?.freshness?.state || "missing")} <span class="meta-kpi">${number(row?.games)} игр · ${percent(row?.popularity_pct)} меты</span></div></div>${unverified ? '<p class="meta-note">Период может включать данные до текущего патча; сигналы по нему не подтверждаются автоматически.</p>' : ""}<div class="meta-subtabs" role="group" aria-label="Данные архетипа">${[["decks","Сборки"],["cards","Карты"],["matchups","Матчапы"],["history","История"]].map(([id,label]) => `<button class="mini-action ${state.detailTab === id ? "active" : ""}" data-meta-detail-tab="${id}">${label}</button>`).join("")}</div><div id="meta-detail-body"></div>`;
    renderDetailTab().catch(showError);
  }

  async function renderDetailTab() {
    const body = inside("#meta-detail-body");
    if (!body) return;
    if (state.detailTab === "decks") {
      const decks = state.detail.decks.map((deck,index) => ({...deck,index})).filter((deck) => (deck.games || 0) >= state.minDeckGames).sort((a,b) => (b[state.deckSort] ?? -1) - (a[state.deckSort] ?? -1));
      if (!state.detail.decks.length) { body.innerHTML = empty("Сборки пока не собраны", "Обзор меты доступен, подробные варианты появятся после обновления каталога."); return; }
      body.innerHTML = `<div class="meta-inline-filters"><label>Сортировка<select id="meta-deck-sort"><option value="games">По числу игр</option><option value="winrate">По винрейту</option></select></label><label>Минимум игр<select id="meta-deck-games"><option value="0">Все</option><option value="500">500</option><option value="1000">1 000</option></select></label></div><div class="table-scroll"><table class="simple meta-table"><thead><tr><th>Сравнить</th><th>Сборка</th><th>Винрейт</th><th>Игры</th><th>Состав</th></tr></thead><tbody>${decks.map((deck) => `<tr><td><input type="checkbox" data-meta-select-deck="${deck.id}" aria-label="Выбрать сборку ${deck.index + 1}" ${state.selectedDecks.has(deck.id) ? "checked" : ""}></td><td><b>Вариант ${String(deck.index + 1).padStart(2,"0")}</b><small>${deck.fresh ? "Свежие данные" : "Свежесть не подтверждена"}</small></td><td><b class="metric-good">${percent(deck.winrate)}</b><small>${deck.winrate_interval ? `${number(deck.winrate_interval[0],1)}–${number(deck.winrate_interval[1],1)}%` : "нет интервала"}</small></td><td>${number(deck.games)}<small>${deck.sample_sufficient ? "выборка достаточна" : "мало данных"}</small></td><td><button class="mini-action" data-meta-deck="${deck.id}">Открыть</button></td></tr>`).join("") || '<tr><td colspan="5">Нет сборок с такой выборкой.</td></tr>'}</tbody></table></div><div class="meta-compare-bar"><span id="meta-selection-count">Выбрано ${state.selectedDecks.size} из 2</span><button class="mini-action meta-primary" id="meta-compare" ${state.selectedDecks.size !== 2 ? "disabled" : ""}>Сравнить составы</button></div><p class="meta-note">Винрейт описывает сыгранную выборку. Он не доказывает, что конкретная замена карты стала причиной результата.</p>`;
      inside("#meta-deck-sort").value = state.deckSort;
      inside("#meta-deck-games").value = String(state.minDeckGames);
      return;
    }
    if (state.detailTab === "cards") {
      body.innerHTML = state.detail.cards.length ? `<p class="meta-note">Метрики относятся ко всему архетипу, а не к каждой отдельной сборке.</p><div class="table-scroll"><table class="simple meta-table"><thead><tr><th>Карта</th><th>Муллиган</th><th>Добор</th><th>Оставлена</th></tr></thead><tbody>${state.detail.cards.map((card) => `<tr><td><b>${escape(card.card_name || card.id)}</b><small>${card.sample_sufficient ? "выборка достаточна" : "мало наблюдений"}</small></td>${["mulligan","drawn","kept"].map((metric) => `<td><b class="${card[`${metric}_impact`] >= 0 ? "metric-good" : "metric-bad"}">${signed(card[`${metric}_impact`])}</b><small>${number(card[`${metric}_count`])} наблюдений</small></td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("Карточной статистики пока нет", "Выберите другой срез или дождитесь обновления анализа.");
      return;
    }
    if (state.detailTab === "matchups") {
      body.innerHTML = state.detail.matchups.length ? `<p class="meta-note">Матчапы архетипа против классов; для отдельных сборок статистика не смешивается.</p><div class="meta-matchups">${state.detail.matchups.map((matchup) => `<div><span>${escape(matchup.class_label || matchup.class_key)}</span><b class="${matchup.winrate >= 50 ? "metric-good" : "metric-bad"}">${percent(matchup.winrate)}</b><small>${number(matchup.games)} игр</small></div>`).join("")}</div>` : empty("Матчапов пока нет", "Источник не опубликовал полную выборку для этого архетипа.");
      return;
    }
    const timeline = await request(`/archetypes/${encodeURIComponent(state.selected)}/timeline?${scopeQuery()}`);
    if (!timeline.data.length) { body.innerHTML = empty("История ещё не накоплена", "Она появится после нескольких успешных обновлений."); return; }
    const points = [...timeline.data].reverse();
    const width = 680, height = 150;
    const rates = points.map((point) => point.winrate).filter((value) => value != null);
    const low = Math.min(...rates,45) - 1, high = Math.max(...rates,55) + 1;
    const coordinates = points.map((point,index) => { const x = points.length === 1 ? width / 2 : 18 + index * (width - 36) / (points.length - 1); const y = point.winrate == null ? height / 2 : 14 + (high - point.winrate) * (height - 32) / Math.max(1, high - low); return `${x},${y}`; }).join(" ");
    body.innerHTML = `<div class="meta-chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="История винрейта"><line x1="18" y1="${height / 2}" x2="${width - 18}" y2="${height / 2}"></line><polyline points="${coordinates}"></polyline></svg></div><div class="meta-timeline">${points.map((point) => `<button class="mini-action" data-meta-snapshot="${point.snapshot_id}"><b>${percent(point.winrate)}</b><span>${date(point.fetched_at)}</span><small>${number(point.games)} игр</small></button>`).join("")}</div>`;
  }

  async function renderCards() {
    const result = await request(`/cards?${scopeQuery({q:inside("#meta-search").value})}`);
    inside("#meta-content").innerHTML = result.data.length ? `<section class="block"><div class="meta-section-head"><h3>Карты в выбранной мете</h3><span class="badge ok">${result.data.length}</span></div><p class="meta-note">Одна карта может иметь разные показатели в разных архетипах. Общий «рейтинг силы» не рассчитывается.</p><div class="meta-card-grid">${result.data.map((card) => `<article><h4>${escape(card.name)}</h4>${card.archetypes.map((item) => `<div><span>${escape(item.archetype)}<small>${number(item.mulligan_count)} наблюдений</small></span><b class="${item.mulligan_impact >= 0 ? "metric-good" : "metric-bad"}">${signed(item.mulligan_impact)}</b></div>`).join("")}</article>`).join("")}</div></section>` : empty("Карты не найдены", "Измените поиск или выберите срез с карточной статистикой.");
  }

  async function renderSignals() {
    const query = {...state.scope,limit:200};
    if (state.signalState) query.state = state.signalState;
    if (state.signalKind) query.kind = state.signalKind;
    const result = await request(`/signals?${new URLSearchParams(query)}`);
    inside("#meta-content").innerHTML = `<section class="block"><div class="meta-section-head"><h3>Изменения меты</h3><span class="badge ok">${result.total}</span></div><div class="meta-inline-filters"><label>Состояние<select id="meta-signal-state"><option value="">Все</option>${["observing","early","confirmed","rejected"].map((value) => `<option value="${value}">${labels[value]}</option>`).join("")}</select></label><label>Тип<select id="meta-signal-kind"><option value="">Все</option>${Object.entries(signalKinds).map(([value,label]) => `<option value="${value}">${label}</option>`).join("")}</select></label></div><div class="meta-signal-list">${result.data.map((signal) => `<button data-meta-signal="${signal.id}"><span>${badge(signal.state)}<small>${escape(signalKinds[signal.kind] || signal.kind)} · ${date(signal.detected_at)}</small></span><b>${escape(signal.archetype)}</b><p>${escape(signal.reason)}</p></button>`).join("") || empty("Сигналов пока нет", "Система продолжает накапливать историю и проверять изменения.")}</div></section>`;
    inside("#meta-signal-state").value = state.signalState;
    inside("#meta-signal-kind").value = state.signalKind;
  }

  function renderHealth() {
    const slices = state.health?.slices || [];
    const counts = slices.reduce((result,item) => ({...result,[item.state]:(result[item.state] || 0) + 1}), {});
    const budget = state.budget || {};
    inside("#meta-content").innerHTML = `<div class="meta-health-grid"><section class="block"><h3>Свежесть данных</h3><div class="archetype-kpis"><span><b>${counts.fresh || 0}</b>свежих</span><span><b>${counts.stale || 0}</b>устарели</span><span><b>${counts.cached || 0}</b>из кеша</span><span><b>${counts.missing || 0}</b>нет данных</span></div></section><section class="block"><h3>Расход сбора</h3><div class="archetype-kpis"><span><b>${number(budget.monthly_spent_hsguru)}</b>credits учтено</span><span><b>${number(budget.budget_exposure)}</b>с резервом</span><span><b>${escape(budget.state || "unknown")}</b>режим</span><span><b>${budget.month_complete ? "Да" : "Нет"}</b>полный месяц</span></div><p class="meta-note">Платные глубокие сборы включаются только после подтверждения расхода за текущий месяц.</p></section></div><section class="block"><h3>Источники и срезы</h3><div class="table-scroll"><table class="simple meta-table"><thead><tr><th>Источник</th><th>Формат</th><th>Ранг</th><th>Период</th><th>Состояние</th><th>Обновлено</th></tr></thead><tbody>${slices.map((item) => `<tr><td>${escape(item.source_id)}</td><td>${escape(item.format)}</td><td>${escape(ranks[item.rank] || item.rank)}</td><td>${escape(periodLabel(item.period))}</td><td>${badge(item.state)}</td><td>${date(item.fetched_at)}</td></tr>`).join("")}</tbody></table></div></section>`;
  }

  function showDialog(title, content) {
    inside("#meta-dialog-title").textContent = title;
    inside("#meta-dialog-body").innerHTML = content;
    inside("#meta-dialog").showModal();
  }

  async function compareSelected() {
    const [left,right] = [...state.selectedDecks];
    const result = await request(`/archetypes/${encodeURIComponent(state.selected)}/compare?${scopeQuery({left,right})}`);
    const deck = (item,label) => `<div><span>${label}</span><b>${percent(item.winrate)}</b><small>${number(item.games)} игр</small></div>`;
    showDialog("Сравнение сборок", `<div class="meta-duel">${deck(result.left,"Сборка A")}${deck(result.right,"Сборка B")}</div><p class="meta-note">${escape(result.reason)}</p><div class="table-scroll"><table class="simple meta-table"><thead><tr><th>Карта</th><th>A</th><th>B</th></tr></thead><tbody>${result.changes.map((item) => `<tr><td>${escape(item.name || `Карта #${item.dbf_id}`)}</td><td>${item.left_count}</td><td>${item.right_count}</td></tr>`).join("") || '<tr><td colspan="3">Составы совпадают.</td></tr>'}</tbody></table></div>`);
  }

  function bindEvents() {
    const container = root();
    container.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.id === "meta-refresh") load().catch(showError);
      if (button.dataset.metaView) {
        state.view = button.dataset.metaView;
        renderTabs();
        if (!state.ready) return;
        abortRequests();
        renderView().catch(showError);
      }
      if (button.dataset.metaFormat) {
        state.format = button.dataset.metaFormat;
        container.querySelectorAll("[data-meta-format]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
        if (!state.ready) return;
        renderScopeOptions();
        renderView().catch(showError);
      }
      if (button.dataset.metaArch) openArchetype(button.dataset.metaArch).catch(showError);
      if (button.dataset.metaDetailTab) { state.detailTab = button.dataset.metaDetailTab; renderArchetypeDetail(); }
      if (button.id === "meta-compare") compareSelected().catch(showError);
      if (button.dataset.metaDeck) {
        const deck = state.detail.decks.find((item) => item.id === button.dataset.metaDeck);
        showDialog("Состав сборки", `<div class="meta-duel"><div><span>Винрейт</span><b>${percent(deck.winrate)}</b><small>${number(deck.games)} игр</small></div><div><span>Состав</span><b>${deck.composition_complete ? "Полный" : "Частичный"}</b><small>${deck.cards.length} позиций</small></div></div><ul class="cards-list">${deck.cards.map((card) => `<li><b>${card.count}×</b> ${escape(card.name)}${card.sideboard_owner ? " · дополнительный набор" : ""}</li>`).join("")}</ul>${deck.deck_code ? `<div class="meta-compare-bar"><span>Код колоды</span><button class="mini-action meta-primary" data-meta-copy="${escape(deck.deck_code)}">Скопировать</button></div>` : ""}`);
      }
      if (button.dataset.metaCopy) navigator.clipboard.writeText(button.dataset.metaCopy).then(() => { button.textContent = "Скопировано"; }).catch(() => showError(new Error("Браузер не разрешил копирование.")));
      if (button.dataset.metaSnapshot) request(`/snapshots/${button.dataset.metaSnapshot}`).then((snapshot) => showDialog("Основание статистики", `${badge(snapshot.freshness.state)}<h3>${date(snapshot.fetched_at)}</h3><p>${escape(snapshot.format)} · ${escape(ranks[snapshot.rank] || snapshot.rank)} · ${escape(periodLabel(snapshot.period))}</p><p class="meta-note">Покрытие обязательных полей: ${percent(snapshot.coverage * 100)}<br>Источник: ${escape(snapshot.source_id)}</p>`)).catch(showError);
      if (button.dataset.metaSignal) request(`/signals/${button.dataset.metaSignal}`).then((signal) => showDialog("Доказательства сигнала", `${badge(signal.state)}<h3>${escape(signal.archetype)}</h3><p>${escape(signal.reason)}</p><p class="meta-note">Снимков: ${signal.evidence.snapshot_ids.length} · правило ${escape(signal.rule_version)}<br>Уверенность — оценка правила, а не вероятность истинности.</p>`)).catch(showError);
      if (button.hasAttribute("data-meta-close")) inside("#meta-dialog").close();
    });
    container.addEventListener("change", (event) => {
      const input = event.target;
      if (input.id === "meta-scope" && state.ready) { abortRequests(); selectScope(); renderView().catch(showError); }
      if (input.matches("[data-meta-select-deck]")) {
        if (input.checked && state.selectedDecks.size >= 2) { input.checked = false; return; }
        if (input.checked) state.selectedDecks.add(input.dataset.metaSelectDeck); else state.selectedDecks.delete(input.dataset.metaSelectDeck);
        inside("#meta-selection-count").textContent = `Выбрано ${state.selectedDecks.size} из 2`;
        inside("#meta-compare").disabled = state.selectedDecks.size !== 2;
      }
      if (input.id === "meta-deck-sort" || input.id === "meta-deck-games") { state.deckSort = inside("#meta-deck-sort").value; state.minDeckGames = Number(inside("#meta-deck-games").value); state.selectedDecks.clear(); renderDetailTab().catch(showError); }
      if (input.id === "meta-signal-state" || input.id === "meta-signal-kind") { state.signalState = inside("#meta-signal-state").value; state.signalKind = inside("#meta-signal-kind").value; abortRequests(); renderSignals().catch(showError); }
    });
    let searchTimer;
    container.addEventListener("input", (event) => {
      if (event.target.id !== "meta-search") return;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { if (state.view === "archetypes") renderArchetypes(); if (state.view === "cards") { abortRequests(); renderCards().catch(showError); } }, 180);
    });
  }

  const container = root();
  if (!container) return;
  bindEvents();
  load().catch(showError);
})();
