const $ = (sel) => document.querySelector(sel);

const STATE_CLASS = {
  ok: "badge-ok",
  fetch_error: "badge-err",
  quality_error: "badge-warn",
  http_error: "badge-err",
  blocked_by_protection: "badge-warn",
  proxy_required: "badge-warn",
  timed_out: "badge-err",
  never_fetched: "badge-muted",
  partial: "badge-warn",
};

const LEVEL_CLASS = {
  info: "level-info",
  warn: "level-warn",
  error: "level-err",
};

function badge(state) {
  const cls = STATE_CLASS[state] || "badge-muted";
  return `<span class="badge ${cls}">${state || "?"}</span>`;
}

function levelBadge(level) {
  const cls = LEVEL_CLASS[level] || "level-info";
  return `<span class="level-pill ${cls}">${level || "info"}</span>`;
}

function fmtTs(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("ru-RU", { hour12: false });
  } catch {
    return ts;
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
}

function getApiKey() {
  return localStorage.getItem("hs_ops_api_key") || "";
}

function opsHeaders() {
  const key = getApiKey();
  const headers = { Accept: "application/json" };
  if (key) headers["X-API-Key"] = key;
  return headers;
}

async function opsFetch(url) {
  const res = await fetch(url, { headers: opsHeaders() });
  if (res.status === 401) {
    throw new Error("401: укажите API-ключ в поле выше");
  }
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
}

function chip(label, value, kind) {
  return `<span class="ops-chip ops-chip-${kind || "default"}"><span class="k">${esc(label)}</span><span class="v">${esc(String(value))}</span></span>`;
}

async function loadSummary() {
  const hours = $("#filter-hours").value;
  return opsFetch(`/ops/summary?since_hours=${hours}`);
}

async function loadEvents() {
  const params = new URLSearchParams();
  params.set("limit", $("#filter-limit").value || "200");
  const hours = $("#filter-hours").value;
  if (hours) params.set("since_hours", hours);
  const sid = $("#filter-source").value;
  const grp = $("#filter-group").value;
  const lvl = $("#filter-level").value;
  if (sid) params.set("source_id", sid);
  if (grp) params.set("action_group", grp);
  if (lvl) params.set("level", lvl);
  return opsFetch(`/ops/events?${params}`);
}

async function loadTrace(traceId) {
  return opsFetch(`/ops/trace/${encodeURIComponent(traceId)}`);
}

function renderSummary(data) {
  const states = data.sources_by_state || {};
  const ok = states.ok || 0;
  const total = Object.values(states).reduce((a, b) => a + b, 0);
  const auth = data.hsreplay_auth || {};
  const freshness = data.freshness || {};
  const stale = freshness.stale_count ?? (data.stale_datasets || []).length;
  const cached = freshness.cached_count ?? (data.cached_sources || []).length;
  const cachedFailed = freshness.cached_after_failure_count ?? (data.cached_after_failure_sources || []).length;
  $("#ops-stats").innerHTML =
    `Источников OK: <strong>${ok}</strong> / ${total} · ` +
    `событий: <strong>${data.events_total}</strong> · ` +
    `stale: <strong>${stale}</strong> · ` +
    `cache: <strong>${cached}</strong>` +
    (cachedFailed ? ` (<span class="err-text">${cachedFailed} live failed</span>)` : "") +
    ` · ` +
    `HSReplay auth: <strong>${auth.is_authenticated ? "ok" : "—"}</strong>` +
    (auth.warning ? ` <span class="muted">(${esc(auth.warning)})</span>` : "") +
    ` · <span class="muted">${esc(data.log_path)}</span>`;

  const levels = data.events_by_level || {};
  $("#level-summary").innerHTML = Object.entries(levels)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => chip(k, v, k === "error" ? "err" : k === "warn" ? "warn" : "default"))
    .join("") || "<span class='muted'>—</span>";

  const groups = data.events_by_group || {};
  $("#group-summary").innerHTML = Object.entries(groups)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 16)
    .map(([k, v]) => chip(k, v))
    .join("");

  const actionErr = data.action_errors || {};
  $("#action-errors").innerHTML = Object.entries(actionErr)
    .map(([k, v]) => chip(k, v, "err"))
    .join("") || "<span class='muted'>нет error action</span>";

  const dbFailures = data.db_store_failures || {};
  if (Object.keys(dbFailures).length) {
    $("#action-errors").innerHTML +=
      " " +
      Object.entries(dbFailures)
        .map(([k, v]) => chip(`db:${k}`, v, "err"))
        .join("");
  }

  const bchips = [];
  for (const [k, v] of Object.entries(data.backend_failures || {})) {
    bchips.push(chip(k, v, "err"));
  }
  $("#backend-summary").innerHTML = bchips.join("") || "<span class='muted'>—</span>";

  const traffic = data.last_traffic || {};
  const tchips = [];
  if (traffic.body_mb_lower_bound != null) {
    tchips.push(chip("body MB", traffic.body_mb_lower_bound));
  }
  if (traffic.iproyal_body_mb_estimate != null) {
    tchips.push(chip("IPRoyal est. MB", traffic.iproyal_body_mb_estimate));
  }
  if (traffic.sources_with_body != null) {
    tchips.push(chip("sources", traffic.sources_with_body));
  }
  $("#traffic-summary").innerHTML = tchips.join("") || "<span class='muted'>пока нет данных</span>";

  const weak = (data.weak_sources || []).filter((item) => item.risk !== "low").slice(0, 10);
  $("#weak-sources").innerHTML = weak.length
    ? weak
        .map((item) => {
          const score = item.quality_score == null ? "—" : Number(item.quality_score).toFixed(2);
          return `
            <div class="weak-source weak-source-${esc(item.risk)}">
              <div><code>${esc(item.source_id)}</code> ${badge(item.state)} <span class="muted">${esc(item.risk)}</span></div>
              <div class="muted">backend: ${esc(item.last_backend || "—")} · score: ${esc(score)} · failures: ${esc(item.failures_24h || 0)} · preserved: ${esc(item.preserved_count_24h || 0)}</div>
              <div>${esc(item.recommendation || "Monitor source")}</div>
            </div>`;
        })
        .join("")
    : "<span class='muted'>нет weak sources за выбранный период</span>";

  const groupSelect = $("#filter-group");
  const prevG = groupSelect.value;
  const groupsList = data.action_groups || Object.keys(groups);
  groupSelect.innerHTML =
    '<option value="">все</option>' +
    groupsList.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("");
  if ([...groupSelect.options].some((o) => o.value === prevG)) groupSelect.value = prevG;

  const sourceSelect = $("#filter-source");
  const prev = sourceSelect.value;
  const ids = [...new Set((data.vulnerabilities || []).map((v) => v.source_id))].sort();
  sourceSelect.innerHTML =
    '<option value="">все</option>' +
    ids.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join("");
  if (ids.includes(prev)) sourceSelect.value = prev;
}

function renderVulnTable(data) {
  const filter = $("#filter-state").value;
  const rows = (data.vulnerabilities || []).filter((v) => {
    if (filter === "problem") return v.state !== "ok";
    if (filter === "ok") return v.state === "ok";
    return true;
  });

  const tbody = $("#vuln-table tbody");
  tbody.innerHTML = rows
    .map((v) => {
      const traceBtn = v.last_trace_id
        ? `<button type="button" class="ops-link trace-open" data-trace="${esc(v.last_trace_id)}">${esc(v.last_trace_id.slice(0, 12))}…</button>`
        : "—";
      return `
    <tr class="${v.state !== "ok" || v.serving_cached_dataset || v.is_stale ? "row-problem" : ""}">
      <td><code>${esc(v.source_id)}</code></td>
      <td>${badge(v.state)}${v.serving_cached_dataset ? " " + badge("cached") : ""}${v.is_stale ? " " + badge("stale") : ""}</td>
      <td>${esc(v.backend || "—")}</td>
      <td>${v.failures_24h ? `<strong class="err-text">${v.failures_24h}</strong>` : "0"}</td>
      <td>${traceBtn}</td>
      <td class="detail-cell" title="${esc(v.detail_preview || "")}">${esc((v.detail_preview || "—").slice(0, 100))}</td>
    </tr>`;
    })
    .join("");

  tbody.querySelectorAll(".trace-open").forEach((btn) => {
    btn.onclick = () => openTrace(btn.dataset.trace);
  });
}

function renderEvents(events) {
  const tbody = $("#events-table tbody");
  tbody.innerHTML = [...events]
    .reverse()
    .map(
      (e) => `
    <tr class="row-${esc(e.level || "info")} ${e.action?.includes("fail") ? "row-action-fail" : ""}">
      <td class="num">${e.step ?? "—"}</td>
      <td class="small nowrap">${fmtTs(e.ts)}</td>
      <td>${levelBadge(e.level)}</td>
      <td><code class="action-code">${esc(e.action || e.event)}</code>
        ${e.trace_id ? `<button type="button" class="ops-link trace-open" data-trace="${esc(e.trace_id)}">↗</button>` : ""}</td>
      <td><code>${esc(e.source_id || "—")}</code></td>
      <td>${esc(e.backend || "—")}</td>
      <td class="num">${e.duration_ms != null ? Math.round(e.duration_ms) : "—"}</td>
      <td class="detail-cell" title="${esc(e.detail || "")}">${esc((e.detail || e.error_type || "—").slice(0, 140))}</td>
    </tr>`
    )
    .join("");

  tbody.querySelectorAll(".trace-open").forEach((btn) => {
    btn.onclick = (ev) => {
      ev.stopPropagation();
      openTrace(btn.dataset.trace);
    };
  });
}

async function openTrace(traceId) {
  if (!traceId) return;
  $("#trace-panel").classList.add("open");
  $("#trace-title").textContent = traceId;
  const tbody = $("#trace-table tbody");
  tbody.innerHTML = "<tr><td colspan='8' class='muted'>Загрузка…</td></tr>";
  try {
    const data = await loadTrace(traceId);
    if (!data.found) {
      tbody.innerHTML = "<tr><td colspan='8'>Trace не найден</td></tr>";
      return;
    }
    tbody.innerHTML = data.events
      .map(
        (e) => `
      <tr class="row-${esc(e.level)}">
        <td class="num">${e.step ?? "—"}</td>
        <td class="small nowrap">${fmtTs(e.ts)}</td>
        <td>${levelBadge(e.level)}</td>
        <td><code>${esc(e.action)}</code></td>
        <td>${esc(e.backend || "—")}</td>
        <td class="num">${e.http_status ?? "—"}</td>
        <td class="num">${e.duration_ms != null ? Math.round(e.duration_ms) : "—"}</td>
        <td class="detail-cell" title="${esc(e.detail || "")}">${esc((e.detail || "—").slice(0, 200))}</td>
      </tr>`
      )
      .join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan='8' class='err-text'>${esc(err.message)}</td></tr>`;
  }
}

async function refreshAll() {
  $("#btn-refresh").disabled = true;
  try {
    const summary = await loadSummary();
    renderSummary(summary);
    renderVulnTable(summary);
    const { events } = await loadEvents();
    renderEvents(events);
  } catch (err) {
    $("#ops-stats").innerHTML = `<span class="err-text">Ошибка: ${esc(err.message)}</span>`;
  } finally {
    $("#btn-refresh").disabled = false;
  }
}

let timer = null;

function setupAuto() {
  if (timer) clearInterval(timer);
  if ($("#auto-refresh").checked) {
    timer = setInterval(refreshAll, 30000);
  }
}

const apiKeyInput = $("#filter-api-key");
if (apiKeyInput) {
  apiKeyInput.value = getApiKey();
  apiKeyInput.addEventListener("change", () => {
    localStorage.setItem("hs_ops_api_key", apiKeyInput.value.trim());
  });
}

$("#btn-refresh").addEventListener("click", refreshAll);
$("#filter-state").addEventListener("change", refreshAll);
$("#filter-hours").addEventListener("change", refreshAll);
$("#filter-source").addEventListener("change", refreshAll);
$("#filter-group").addEventListener("change", refreshAll);
$("#filter-level").addEventListener("change", refreshAll);
$("#filter-limit").addEventListener("change", refreshAll);
$("#auto-refresh").addEventListener("change", setupAuto);
$("#trace-close").addEventListener("click", () => {
  $("#trace-panel").classList.remove("open");
});

refreshAll();
setupAuto();
