"use strict";
(() => {
  const $ = (q) => document.querySelector(q);
  const escape = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const num = (v, digits = 0) => v == null ? "—" : Number(v).toLocaleString("ru-RU", {maximumFractionDigits: digits, minimumFractionDigits: digits});
  const pct = (v) => v == null ? "—" : `${num(v, 1)}%`;
  const signed = (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${num(v, 1)}`;
  const date = (v) => v ? new Date(v).toLocaleString("ru-RU", {day:"2-digit", month:"short", hour:"2-digit", minute:"2-digit"}) : "—";
  const ranks = {all:"Все ранги",legend:"Legend",diamond_4to1:"Diamond 4–1",top_legend:"Top Legend",top_5k:"Top 5K",unknown:"Ранг не указан"};
  const periods = {past_week:"7 дней",past_day:"24 часа",unknown:"Период не указан"};
  const states = {fresh:"Свежие",stale:"Устарели",cached:"Последние доступные",missing:"Нет данных",observing:"Наблюдение",early:"Ранний",confirmed:"Подтверждён",rejected:"Не подтвердился"};
  const kinds = {popularity_rise:"Рост популярности",popularity_drop:"Снижение популярности",winrate_shift:"Изменение винрейта",matchup_shift:"Изменение матчапа",deck_variant_detected:"Новая сборка",streamer_burst:"Интерес стримеров"};
  const badge = (state) => `<span class="badge ${escape(state)}">${escape(states[state] || state)}</span>`;
  let key = "", epoch = 0, view = "archetypes", format = "standard", scope = null;
  let health = null, budget = null, rows = [], selected = null, detail = null, tab = "decks", selectedDecks = new Set(), archRequest = 0;
  let signalState = "", signalKind = "", deckSort = "games", minDeckGames = 0;
  const controllers = new Set();

  function renew() { epoch++; controllers.forEach((c) => c.abort()); controllers.clear(); }
  function announce(message) { $("#status").textContent = message; }
  function error(err) {
    if (err.name === "AbortError") return;
    $("#error").textContent = err.message || "Не удалось загрузить данные. Повторите попытку.";
    $("#error").hidden = false;
  }
  async function request(path) {
    const captured = epoch, controller = new AbortController();
    controllers.add(controller);
    try {
      const response = await fetch(`/v1/meta-intelligence${path}`, {headers:{"X-API-Key":key,Accept:"application/json"},cache:"no-store",signal:controller.signal});
      if (captured !== epoch) throw new DOMException("Cancelled", "AbortError");
      if (!response.ok) {
        if (response.status === 401) { logout(); throw new Error("Ключ доступа не подошёл. Введите действующий ключ."); }
        if (response.status === 503) throw new Error("Лаборатория ещё не включена в настройках сервиса.");
        if (response.status === 404) throw new Error("Для выбранного среза этих данных пока нет.");
        throw new Error("Сервис не смог вернуть данные. Попробуйте обновить страницу.");
      }
      return await response.json();
    } finally { controllers.delete(controller); }
  }
  function query(extra = {}) { return new URLSearchParams({...scope,...extra}).toString(); }
  function empty(title, description) { return `<div class="empty"><h3>${escape(title)}</h3><p>${escape(description)}</p></div>`; }
  function scopeOptions() {
    const available = (health.scopes || []).filter((s) => s.format === format);
    available.sort((a,b) => Number(b.rank === "legend")-Number(a.rank === "legend") || b.latest.localeCompare(a.latest));
    const previous = scope && JSON.stringify([scope.rank,scope.period,scope.patch]);
    $("#scope-select").innerHTML = available.length ? available.map((s) => `<option value="${escape(JSON.stringify([s.rank,s.period,s.patch]))}">${escape(ranks[s.rank] || s.rank)} · ${escape(periods[s.period] || s.period)} · ${escape(s.patch === "unknown" ? "Патч не указан" : s.patch.replace("patch_", "Патч "))}</option>`).join("") : '<option value="">Нет сохранённых срезов</option>';
    if (available.some((s) => JSON.stringify([s.rank,s.period,s.patch]) === previous)) $("#scope-select").value = previous;
    applyScope();
  }
  function applyScope() {
    const value = $("#scope-select").value;
    const parts = value ? JSON.parse(value) : null;
    scope = parts ? {format,rank:parts[0],period:parts[1],patch:parts[2]} : null;
    selected = null; detail = null; selectedDecks.clear();
  }
  function summary() {
    $("#summary").innerHTML = `<span><strong>${num(health.scopes.reduce((n,s) => n+s.snapshots,0))}</strong> снимков в истории</span><span><strong>${num(budget.monthly_spent_hsguru)}</strong> credits учтено за месяц</span><span>${budget.month_complete ? "Учёт за полный месяц" : "Учёт начат не с начала месяца"}</span>`;
    $("#demo-notice").hidden = !health.demo;
  }
  async function load() {
    renew(); $("#error").hidden = true; $("#content").setAttribute("aria-busy", "true");
    $("#refresh").disabled = true;
    try {
      [health,budget] = await Promise.all([request("/health"),request("/budget")]);
      scopeOptions(); summary(); await renderView();
    } finally { $("#refresh").disabled = false; $("#content").setAttribute("aria-busy", "false"); }
  }
  function renderNav() {
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    $("#page-title").textContent = {archetypes:"Исследование меты",cards:"Карты в мете",signals:"Изменения меты",health:"Качество и стоимость данных"}[view];
    $("#filters").hidden = view === "health";
    $("#search").placeholder = view === "cards" ? "Название карты…" : "Название архетипа…";
  }
  async function renderView() {
    renderNav(); $("#error").hidden = true;
    if (view === "health") { renderHealth(); return; }
    if (view === "signals") { await renderSignals(); return; }
    if (!scope) { $("#content").innerHTML = `<div class="panel">${empty("История ещё не собрана", "После первого успешного сбора здесь появятся архетипы, варианты колод и статистика карт.")}</div>`; return; }
    if (view === "cards") { await renderCards(); return; }
    const result = await request(`/archetypes?${query({limit:500})}`);
    rows = result.data; renderArchetypes();
  }
  function renderArchetypes() {
    const term = $("#search").value.toLocaleLowerCase("ru-RU");
    const visible = rows.filter((r) => r.archetype.toLocaleLowerCase("ru-RU").includes(term));
    if (!visible.length) { $("#content").innerHTML = `<div class="panel">${empty("Архетипы не найдены", "Измените поиск или выберите другой срез данных.")}</div>`; return; }
    $("#content").innerHTML = `<div class="workspace-grid"><section class="panel"><div class="panel-header"><h3>Архетипы</h3><span class="badge">${visible.length}</span></div><div class="arch-list">${visible.map((r) => `<button class="arch-row ${r.archetype === selected ? "selected" : ""}" data-arch="${escape(r.archetype)}"><strong>${escape(r.archetype)}</strong><small>${num(r.games)} игр · ${r.decks} сборок</small><span class="wr">${pct(r.winrate)}</span></button>`).join("")}</div></section><section class="panel" id="detail">${empty("Выберите архетип", "Откройте его сборки, карты и историю.")}</section></div>`;
    openArch(visible.some((r) => r.archetype === selected) ? selected : visible[0].archetype).catch(error);
  }
  async function openArch(name) {
    const sequence = ++archRequest; selected = name; selectedDecks.clear();
    document.querySelectorAll("[data-arch]").forEach((b) => b.classList.toggle("selected", b.dataset.arch === name));
    $("#detail").innerHTML = empty("Загружаем данные…", "");
    const result = await request(`/archetypes/${encodeURIComponent(name)}?${query()}`);
    if (sequence !== archRequest || !$("#detail")) return;
    detail = result; renderDetail();
  }
  function renderDetail() {
    const row = rows.find((r) => r.archetype === selected);
    $("#detail").innerHTML = `<div class="detail-title"><span class="eyebrow">${escape(ranks[scope.rank] || scope.rank)} · ${escape(periods[scope.period] || scope.period)}</span><h2>${escape(detail.archetype)}</h2>${badge(row?.freshness?.state || "missing")} <span class="hint">${num(row?.games)} игр · ${pct(row?.popularity_pct)} меты</span>${detail.evidence.some((e) => e.patch_scope !== "single_patch") ? '<p class="hint">Период может включать игры до указанного патча. Подтверждение сигналов ограничено.</p>' : ""}</div><div class="detail-tabs" role="group" aria-label="Данные архетипа">${[["decks","Сборки"],["cards","Карты"],["matchups","Матчапы"],["history","История"]].map(([id,label]) => `<button data-tab="${id}" aria-pressed="${tab === id}">${label}</button>`).join("")}</div><div id="detail-body"></div>`;
    renderTab().catch(error);
  }
  async function renderTab() {
    const body = $("#detail-body");
    if (tab === "decks") {
      const visibleDecks = detail.decks.map((d,i) => ({...d, displayIndex:i})).filter((d) => (d.games || 0) >= minDeckGames).sort((a,b) => (b[deckSort] ?? -1)-(a[deckSort] ?? -1));
      body.innerHTML = detail.decks.length ? `<div class="signal-controls"><label>Сортировка<select id="deck-sort"><option value="games" ${deckSort === "games" ? "selected" : ""}>По числу игр</option><option value="winrate" ${deckSort === "winrate" ? "selected" : ""}>По винрейту</option></select></label><label>Выборка<select id="deck-games"><option value="0" ${minDeckGames === 0 ? "selected" : ""}>Все сборки</option><option value="500" ${minDeckGames === 500 ? "selected" : ""}>От 500 игр</option><option value="1000" ${minDeckGames === 1000 ? "selected" : ""}>От 1 000 игр</option></select></label></div><div class="table-scroll"><table><thead><tr><th><span class="sr-only">Сравнить</span></th><th>Сборка</th><th class="numeric">Винрейт</th><th class="numeric">Игры</th><th>Состав</th></tr></thead><tbody>${visibleDecks.map((d) => { const i = d.displayIndex; return `<tr><td><input class="table-check" type="checkbox" data-select-deck="${d.id}" aria-label="Выбрать сборку ${i+1} для сравнения" ${selectedDecks.has(d.id) ? "checked" : ""}></td><td><strong>Вариант ${String(i+1).padStart(2,"0")}</strong><small>${d.fresh ? "Свежие данные" : "Свежесть не подтверждена"}</small></td><td class="numeric"><strong class="positive">${pct(d.winrate)}</strong><small>${d.winrate_interval ? `${num(d.winrate_interval[0],1)}–${num(d.winrate_interval[1],1)}%` : "Нет интервала"}</small></td><td class="numeric">${num(d.games)}<small>${d.sample_sufficient ? "Достаточная выборка" : "Мало данных"}</small></td><td><button class="table-action" data-deck="${d.id}">Посмотреть</button></td></tr>`; }).join("") || '<tr><td colspan="5">Нет сборок с такой выборкой.</td></tr>'}</tbody></table></div><div class="compare-dock"><span id="selection-count">Выбрано ${selectedDecks.size} из 2 сборок</span><button id="compare-button" class="primary" ${selectedDecks.size !== 2 ? "disabled" : ""}>Сравнить составы →</button></div><p class="hint">Показаны доступные сборки, а не все сыгранные колоды. Диапазон под винрейтом — приблизительный 95% интервал; он не учитывает различия игроков.</p>` : empty("Сборки пока не собраны", "Обзор меты уже доступен. Подробные сборки появятся после сбора выбранного архетипа.");
    } else if (tab === "cards") {
      body.innerHTML = detail.cards.length ? `<p class="hint">Показатели источника для всего архетипа. Не означают эффект карты в каждой конкретной сборке.</p><div class="table-scroll"><table><thead><tr><th>Карта</th><th class="numeric">Муллиган</th><th class="numeric">Добор</th><th class="numeric">Оставлена</th></tr></thead><tbody>${detail.cards.map((c) => `<tr><td>${escape(c.card_name || c.id)}<small>${c.sample_sufficient ? "Выборка достаточна" : "Мало наблюдений"}</small></td>${["mulligan","drawn","kept"].map((m) => `<td class="numeric"><strong class="${c[`${m}_impact`] >= 0 ? "positive" : "negative"}">${signed(c[`${m}_impact`])}</strong><small>${num(c[`${m}_count`])} наблюдений</small></td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("Карточной статистики пока нет", "Выберите другой срез или дождитесь подробного сбора этого архетипа.");
    } else if (tab === "matchups") {
      body.innerHTML = detail.matchups.length ? `<p class="hint">Матчапы архетипа против классов. Они не являются статистикой каждой отдельной сборки.</p><div class="table-scroll"><table><thead><tr><th>Соперник</th><th class="numeric">Винрейт</th><th class="numeric">Игры</th><th class="numeric">Доля</th></tr></thead><tbody>${detail.matchups.map((m) => `<tr><td>${escape(m.class_name || m.class || m.class_key || m.opponent)}</td><td class="numeric"><strong>${pct(m.winrate)}</strong></td><td class="numeric">${num(m.games)}</td><td class="numeric">${pct(m.share_pct)}</td></tr>`).join("")}</tbody></table></div>` : empty("Матчапы пока не собраны", "Данные по соперникам появятся после подробного сбора.");
    } else {
      body.innerHTML = empty("Загружаем историю…", "");
      const name = selected, result = await request(`/archetypes/${encodeURIComponent(selected)}/timeline?${query()}`);
      if (selected !== name || tab !== "history") return;
      body.innerHTML = result.data.length ? `${chart(result.data)}<div class="table-scroll"><table><thead><tr><th>Снимок</th><th class="numeric">Винрейт</th><th class="numeric">Популярность</th><th class="numeric">Игры</th></tr></thead><tbody>${result.data.map((r) => `<tr><td><button class="table-action" data-snapshot="${r.snapshot_id}">${date(r.fetched_at)}</button></td><td class="numeric">${pct(r.winrate)}</td><td class="numeric">${pct(r.popularity_pct)}</td><td class="numeric">${num(r.games)}</td></tr>`).join("")}</tbody></table></div>` : empty("История ещё накапливается", "После нескольких сборов здесь можно будет сравнивать изменения.");
    }
  }
  function chart(data) {
    const eligible = data.filter((r) => r.winrate != null);
    if (!eligible.length) return "";
    const values = eligible.filter((r) => r.source_id === eligible[0].source_id).slice(0,12).reverse();
    if (values.length < 2) return '<p class="hint">Для графика нужны как минимум два снимка одного источника.</p>';
    const low = Math.floor(Math.min(...values.map((r) => r.winrate))-1), high = Math.ceil(Math.max(...values.map((r) => r.winrate))+1);
    const first = Date.parse(values[0].fetched_at), last = Date.parse(values.at(-1).fetched_at);
    const points = values.map((r) => [45+(Date.parse(r.fetched_at)-first)/Math.max(last-first,1)*475,130-(r.winrate-low)/(high-low)*110]);
    return `<div class="chart"><h3>Винрейт во времени</h3><svg viewBox="0 0 550 165" role="img" aria-label="История винрейта: от ${pct(values[0].winrate)} до ${pct(values.at(-1).winrate)}"><line class="chart-grid" x1="45" y1="20" x2="520" y2="20"/><line class="chart-grid" x1="45" y1="130" x2="520" y2="130"/><text x="0" y="24">${high}%</text><text x="0" y="134">${low}%</text><polyline class="chart-line" points="${points.map((p) => p.join(",")).join(" ")}"/>${points.map(([x,y]) => `<circle class="chart-dot" cx="${x}" cy="${y}" r="4"/>`).join("")}<text x="45" y="158">${date(values[0].fetched_at)}</text><text x="520" y="158" text-anchor="end">${date(values.at(-1).fetched_at)}</text></svg></div>`;
  }
  function showDialog(title, html) { $("#comparison-title").textContent = title; $("#comparison-content").innerHTML = html; if (!$("#comparison").open) $("#comparison").showModal(); }
  async function compareSelected() {
    const [left,right] = [...selectedDecks], result = await request(`/archetypes/${encodeURIComponent(selected)}/compare?${query({left,right})}`);
    showDialog("Сравнение сборок", `<div class="duel">${[result.left,result.right].map((d,i) => `<div><span class="eyebrow">СБОРКА ${i === 0 ? "A" : "B"}</span><strong>${pct(d.winrate)}</strong><small>${num(d.games)} игр · ${d.fresh ? "свежие" : "свежесть не подтверждена"}</small></div>`).join("")}</div><p class="hint">${escape(result.reason)} ${!result.comparable ? "Для надёжного сравнения не хватает свежести, состава или размера выборки." : ""}</p><div class="table-scroll"><table><thead><tr><th>Изменения состава</th><th class="numeric">A</th><th class="numeric">B</th></tr></thead><tbody>${result.changes.map((c) => `<tr><td>${escape(c.name)}${c.sideboard_owner ? `<small>Дополнительный набор #${c.sideboard_owner}</small>` : ""}</td><td class="numeric">${c.left_count || "—"}</td><td class="numeric">${c.right_count || "—"}</td></tr>`).join("") || '<tr><td colspan="3">Различий в доступных составах не найдено.</td></tr>'}</tbody></table></div>`);
  }
  async function renderCards() {
    const result = await request(`/cards?${query({q:$("#search").value})}`);
    $("#content").innerHTML = result.data.length ? `<p class="hint">Одна карта — разные архетипы. Показатели не объединяются в общий «рейтинг силы».</p><div class="cards-grid">${result.data.map((c) => `<article class="card-entry"><h3>${escape(c.name)}</h3><ul>${c.archetypes.map((a) => `<li><span>${escape(a.archetype)}<small>${num(a.mulligan_count)} наблюдений · ${escape(states[a.evidence.freshness.state])}</small></span><span><strong class="${a.mulligan_impact >= 0 ? "positive" : "negative"}">${signed(a.mulligan_impact)}</strong><small>муллиган</small></span></li>`).join("")}</ul></article>`).join("")}</div>` : `<div class="panel">${empty("Карты не найдены", "Измените поиск или выберите срез с карточной статистикой.")}</div>`;
  }
  async function renderSignals() {
    const params = scope ? {...scope} : {format};
    if (signalState) params.state = signalState; if (signalKind) params.kind = signalKind;
    const result = await request(`/signals?${new URLSearchParams(params)}`);
    const options = (map,value) => Object.entries(map).map(([k,v]) => `<option value="${k}" ${value === k ? "selected" : ""}>${escape(v)}</option>`).join("");
    $("#content").innerHTML = `<section class="panel"><div class="signal-controls"><label>Состояние<select id="signal-state"><option value="">Все состояния</option>${options(Object.fromEntries(["observing","early","confirmed","rejected"].map((s) => [s,states[s]])),signalState)}</select></label><label>Тип изменения<select id="signal-kind"><option value="">Все типы</option>${options(kinds,signalKind)}</select></label></div>${result.data.length ? result.data.map((s) => `<article class="signal-card">${badge(s.state)} <small>${date(s.detected_at)}</small><h3>${escape(s.archetype)} · ${escape(kinds[s.kind])}</h3><p>${escape(s.reason)}</p><button class="table-action" data-signal="${s.id}">Посмотреть доказательства →</button></article>`).join("") : empty("Подходящих сигналов пока нет", "Система ждёт последовательных наблюдений. Отсутствие сигнала не означает отсутствие сильных колод.")}</section>`;
  }
  function renderHealth() {
    const labels = {normal:"В пределах бюджета",soft_alert:"Приближаемся к лимиту",deep_analysis_throttled:"Глубокий сбор ограничен",hard_limit_reached:"Дополнительный сбор остановлен"};
    $("#content").innerHTML = `<div class="metrics"><section class="metric"><span>Расход с начала учёта в этом месяце</span><strong>${num(budget.monthly_spent_hsguru)} <small>credits</small></strong><progress value="${Math.min(budget.budget_exposure,budget.limits.hard)}" max="${budget.limits.hard}" aria-label="Расход относительно лимита"></progress></section><section class="metric"><span>Прогноз на полный месяц</span><strong>${budget.forecast_month_end == null ? "Недостаточно данных" : num(budget.forecast_month_end)}</strong></section><section class="metric"><span>Состояние бюджета</span><h2>${escape(labels[budget.state])}</h2><span>Порог дополнительных задач: ${num(budget.limits.hard)}</span></section></div><p class="hint">Подтверждено провайдером: ${num(budget.reported_credits)} · Расчётная стоимость: ${num(budget.estimated_credits)} · Резерв и неизвестная стоимость: ${num(budget.reserved_or_unknown_credits)}. Базовый сбор продолжает работать.</p><section class="panel"><div class="panel-header"><h2>Свежесть по срезам</h2><span class="badge">${health.slices.length} срезов</span></div><div class="table-scroll"><table><thead><tr><th>Формат / ранг</th><th>Период</th><th>Последний сбор</th><th>Состояние</th></tr></thead><tbody>${health.slices.map((s) => `<tr><td>${escape(s.format)} · ${escape(ranks[s.rank] || s.rank)}<small>${s.source_id.includes("matchup") ? "Матчапы" : s.source_id.includes("details") || s.source_id.includes("analysis") ? "Подробная аналитика" : "Мета"}</small></td><td>${escape(periods[s.period] || s.period)}</td><td>${date(s.fetched_at)}</td><td>${badge(s.state)}<small>${s.reasons.includes("latest_source_failure") ? "Последний сбор завершился ошибкой" : s.reasons.includes("expected_interval_exceeded") ? "Пропущен ожидаемый интервал" : ""}</small></td></tr>`).join("")}</tbody></table></div></section>`;
  }
  function logout() {
    renew(); key = ""; health = null; budget = null; rows = []; detail = null; selected = null; selectedDecks.clear();
    $("#application").hidden = true; $("#login").hidden = false; $("#logout").hidden = true;
    $("#content").replaceChildren(); $("#summary").replaceChildren(); $("#comparison-content").replaceChildren();
    $("#comparison").close(); $("#api-key").value = "";
    $("#api-key").focus();
  }
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault(); key = $("#api-key").value.trim(); $("#api-key").value = ""; $("#error").hidden = true;
    const button = event.submitter; button.disabled = true;
    try { await load(); $("#login").hidden = true; $("#application").hidden = false; $("#logout").hidden = false; announce("Лаборатория открыта"); }
    catch (err) { key = ""; error(err); } finally { button.disabled = false; }
  });
  $("#logout").addEventListener("click", logout);
  $("#refresh").addEventListener("click", () => load().catch(error));
  $("#close-comparison").addEventListener("click", () => $("#comparison").close());
  $("#navigation").addEventListener("click", (event) => { const b = event.target.closest("[data-view]"); if (!b || !key || !health) return; renew(); view = b.dataset.view; renderView().catch(error); });
  document.querySelectorAll("[data-format]").forEach((b) => b.addEventListener("click", () => { if (!health) return; renew(); format = b.dataset.format; document.querySelectorAll("[data-format]").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); scopeOptions(); renderView().catch(error); }));
  $("#scope-select").addEventListener("change", () => { renew(); applyScope(); renderView().catch(error); });
  let searchTimer;
  $("#search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { if (!key) return; if (view === "archetypes") renderArchetypes(); else if (view === "cards") { renew(); renderCards().catch(error); } }, 180); });
  $("#content").addEventListener("change", (event) => {
    const input = event.target;
    if (input.matches("[data-select-deck]")) {
      if (input.checked && selectedDecks.size >= 2) { input.checked = false; announce("Можно сравнить две сборки. Сначала снимите один выбор."); return; }
      if (input.checked) selectedDecks.add(input.dataset.selectDeck); else selectedDecks.delete(input.dataset.selectDeck);
      $("#selection-count").textContent = `Выбрано ${selectedDecks.size} из 2 сборок`; $("#compare-button").disabled = selectedDecks.size !== 2;
    }
    if (input.id === "deck-sort" || input.id === "deck-games") { deckSort = $("#deck-sort").value; minDeckGames = Number($("#deck-games").value); selectedDecks.clear(); renderTab().catch(error); document.getElementById(input.id).focus(); }
    if (input.id === "signal-state" || input.id === "signal-kind") { signalState = $("#signal-state").value; signalKind = $("#signal-kind").value; renew(); renderSignals().catch(error); }
  });
  $("#content").addEventListener("click", (event) => {
    const button = event.target.closest("button"); if (!button) return;
    if (button.dataset.arch) openArch(button.dataset.arch).catch(error);
    if (button.dataset.tab) { tab = button.dataset.tab; renderDetail(); document.querySelector(`[data-tab="${tab}"]`).focus(); }
    if (button.id === "compare-button") compareSelected().catch(error);
    if (button.dataset.deck) {
      const deck = detail.decks.find((d) => d.id === button.dataset.deck);
      showDialog("Состав сборки", `<p class="hint">${pct(deck.winrate)} · ${num(deck.games)} игр. ${deck.composition_complete ? "Состав доступен полностью." : "Полный состав недоступен."}</p><ul class="composition">${deck.cards.map((c) => `<li><span>${escape(c.name)}${c.sideboard_owner ? " · дополнительный набор" : ""}</span><strong>×${c.count}</strong></li>`).join("")}</ul>${deck.deck_code ? `<div class="compare-dock"><span>Код колоды</span><button class="primary" id="copy-code" data-code="${escape(deck.deck_code)}">Скопировать</button></div>` : ""}`);
    }
    if (button.dataset.snapshot) request(`/snapshots/${button.dataset.snapshot}`).then((s) => showDialog("Основание статистики", `<div class="signal-card">${badge(s.freshness.state)}<h3>${date(s.fetched_at)}</h3><p>${escape(s.format)} · ${escape(ranks[s.rank] || s.rank)} · ${escape(periods[s.period] || s.period)}</p><p>Патч: ${escape(s.patch)}<br>Покрытие обязательных полей: ${pct(s.coverage*100)}</p><small>Снимок ${escape(s.id.slice(0,16))} · ${escape(s.source_id)}</small></div>`)).catch(error);
    if (button.dataset.signal) request(`/signals/${button.dataset.signal}`).then((s) => showDialog("Доказательства сигнала", `<div class="signal-card">${badge(s.state)}<h3>${escape(s.archetype)}</h3><p>${escape(s.reason)}</p><p>Снимков: ${s.evidence.snapshot_ids.length} · Правило: ${escape(s.rule_version)}</p><small>Уверенность — оценка правила, не вероятность истинности.</small></div>${s.history.map((h) => `<div class="signal-card">${date(h.at)} ${badge(h.state)}</div>`).join("")}`)).catch(error);
  });
  $("#comparison-content").addEventListener("click", (event) => { const button = event.target.closest("#copy-code"); if (button) navigator.clipboard.writeText(button.dataset.code).then(() => { button.textContent = "Скопировано"; announce("Код колоды скопирован"); }).catch(() => error(new Error("Браузер не разрешил копирование."))); });
})();
