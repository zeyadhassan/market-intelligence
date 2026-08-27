# ruff: noqa: E501
"""Self-contained local assets for the operational analyst workbench."""

WORKBENCH_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>FI Intel Workbench</title>
  <link rel="stylesheet" href="/workbench/assets/workbench.css">
</head>
<body>
  <a class="skip-link" href="#signal-detail">Skip to signal detail</a>
  <header class="topbar">
    <div class="brand-block">
      <span class="brand-mark" aria-hidden="true">FI</span>
      <div><strong>FI Intel</strong><span>Analyst Workbench</span></div>
    </div>
    <div class="toolbar">
      <label>Desk<select id="desk-select" aria-label="Desk"></select></label>
      <label>Status<select id="status-filter" aria-label="Signal status">
        <option value="">Active and closed</option>
        <option value="candidate">Candidate</option>
        <option value="confirmed">Confirmed</option>
        <option value="reviewed">Reviewed</option>
        <option value="published">Published</option>
        <option value="suppressed">Suppressed</option>
        <option value="expired">Expired</option>
        <option value="withdrawn">Withdrawn</option>
      </select></label>
      <button id="refresh-button" class="icon-button" type="button" title="Refresh inbox"
              aria-label="Refresh inbox"><span aria-hidden="true">&#8635;</span></button>
      <button id="logout-button" class="icon-button" type="button" title="End session"
              aria-label="End session"><span aria-hidden="true">&#215;</span></button>
    </div>
  </header>

  <main class="workspace">
    <section class="inbox-panel" aria-labelledby="inbox-heading">
      <div class="panel-heading">
        <div><h1 id="inbox-heading">Signal inbox</h1><span id="signal-count">0</span></div>
        <label class="search-field"><span class="sr-only">Filter signals</span>
          <input id="signal-search" type="search" placeholder="Filter entities or patterns">
        </label>
      </div>
      <div id="signal-list" class="signal-list" aria-live="polite"></div>
    </section>

    <section id="signal-detail" class="detail-panel" aria-labelledby="detail-heading">
      <div class="panel-heading detail-heading">
        <div><span class="eyebrow" id="detail-pattern">No signal selected</span>
          <h2 id="detail-heading">Select a signal</h2></div>
        <span id="detail-status" class="status-badge neutral">Waiting</span>
      </div>
      <dl class="signal-metrics">
        <div><dt>Score</dt><dd id="detail-score">-</dd></div>
        <div><dt>As of</dt><dd id="detail-as-of">-</dd></div>
        <div><dt>Changed</dt><dd id="detail-changed">-</dd></div>
        <div><dt>Feedback</dt><dd id="detail-feedback">-</dd></div>
      </dl>

      <div class="decision-strip" aria-label="Signal decision controls">
        <label for="feedback-reason">Decision note</label>
        <textarea id="feedback-reason" rows="2" maxlength="2000"
                  placeholder="Record the evidence-based reason"></textarea>
        <div class="decision-actions">
          <button type="button" data-verdict="approve" class="approve">Approve</button>
          <button type="button" data-verdict="needs_review">Needs review</button>
          <button type="button" data-verdict="reject" class="reject">Reject</button>
          <select id="close-reason" aria-label="Close reason">
            <option value="actioned">Actioned</option>
            <option value="not_relevant">Not relevant</option>
            <option value="duplicate">Duplicate</option>
            <option value="stale">Stale</option>
            <option value="invalid">Invalid</option>
          </select>
          <button id="close-signal" type="button" class="quiet">Close</button>
        </div>
      </div>

      <section class="evidence-section" aria-labelledby="evidence-heading">
        <div class="section-heading"><h3 id="evidence-heading">Evidence</h3>
          <span id="evidence-count">0 spans</span></div>
        <div id="evidence-list" class="evidence-list"></div>
      </section>
    </section>

    <aside class="entity-panel" aria-labelledby="entity-heading">
      <div class="panel-heading"><div><span class="eyebrow">Entity profile</span>
        <h2 id="entity-heading">No entity selected</h2></div></div>
      <dl id="entity-identifiers" class="identifier-list"></dl>
      <div class="section-heading"><h3>Assertion timeline</h3>
        <span id="timeline-count">0 facts</span></div>
      <ol id="entity-timeline" class="timeline"></ol>
    </aside>
  </main>

  <div id="toast" class="toast" role="status" aria-live="polite"></div>
  <dialog id="session-dialog">
    <form id="session-form" method="dialog">
      <span class="brand-mark" aria-hidden="true">FI</span>
      <h2>Analyst session</h2>
      <label>Bearer token<input id="token-input" type="password" autocomplete="off" required></label>
      <p id="session-error" role="alert"></p>
      <button type="submit">Connect</button>
    </form>
  </dialog>
  <script src="/workbench/assets/workbench.js" defer></script>
</body>
</html>
"""


WORKBENCH_CSS = """:root {
  color-scheme: light;
  --ink: #20252b;
  --muted: #626b73;
  --line: #d7dce0;
  --surface: #ffffff;
  --wash: #f4f6f5;
  --green: #176b4d;
  --green-soft: #e5f3ec;
  --red: #a33a33;
  --red-soft: #f8e8e6;
  --amber: #8a5a12;
  --amber-soft: #fff1d6;
  --blue: #245ea8;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; background: var(--wash); color: var(--ink); }
button, input, select, textarea { font: inherit; letter-spacing: 0; }
button, select, input, textarea { border: 1px solid #aeb6bd; border-radius: 4px; }
button { min-height: 34px; padding: 6px 12px; background: var(--surface); color: var(--ink); cursor: pointer; }
button:hover { border-color: #717b84; background: #f8f9f9; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: 3px solid rgba(36, 94, 168, .25); outline-offset: 1px; border-color: var(--blue);
}
.skip-link { position: fixed; left: 8px; top: -60px; z-index: 20; background: #fff; padding: 8px; }
.skip-link:focus { top: 8px; }
.topbar { height: 64px; padding: 0 18px; display: flex; align-items: center; justify-content: space-between;
  background: #20252b; color: #fff; border-bottom: 3px solid #2a7a59; }
.brand-block { display: flex; align-items: center; gap: 10px; min-width: 190px; }
.brand-block div { display: grid; line-height: 1.1; }
.brand-block span:not(.brand-mark) { color: #c7cdd2; font-size: 12px; margin-top: 3px; }
.brand-mark { display: inline-grid; place-items: center; width: 34px; height: 34px; background: #d7efe3;
  color: #174f3b; border: 1px solid #83b79f; border-radius: 4px; font-weight: 800; }
.toolbar { display: flex; align-items: end; gap: 8px; }
.toolbar label { display: grid; gap: 2px; color: #cbd1d5; font-size: 11px; }
.toolbar select { min-width: 128px; height: 34px; padding: 4px 28px 4px 8px; background: #fff; color: var(--ink); }
.icon-button { width: 36px; height: 36px; padding: 0; font-size: 22px; line-height: 1; }
.workspace { min-height: calc(100vh - 64px); display: grid; grid-template-columns: minmax(280px, 340px) minmax(420px, 1fr) minmax(280px, 360px); }
.inbox-panel, .detail-panel, .entity-panel { min-width: 0; background: var(--surface); }
.inbox-panel { border-right: 1px solid var(--line); }
.detail-panel { padding-bottom: 32px; }
.entity-panel { border-left: 1px solid var(--line); }
.panel-heading { min-height: 76px; padding: 14px 16px; display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); }
.panel-heading h1, .panel-heading h2 { margin: 2px 0 0; font-size: 18px; line-height: 1.25; }
.panel-heading h1 { display: inline; }
.panel-heading > div > span:last-child:not(.eyebrow) { margin-left: 8px; color: var(--muted); font-size: 12px; }
.eyebrow { display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 700; }
.search-field { display: block; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.search-field input { width: 100%; height: 34px; padding: 6px 9px; }
.signal-list { overflow-y: auto; max-height: calc(100vh - 175px); }
.signal-row { width: 100%; min-height: 88px; padding: 11px 14px; display: grid; grid-template-columns: 1fr auto;
  gap: 5px 10px; text-align: left; border: 0; border-bottom: 1px solid #e5e8ea; border-radius: 0; }
.signal-row:hover, .signal-row.selected { background: #edf5f1; box-shadow: inset 3px 0 var(--green); }
.signal-row strong { overflow-wrap: anywhere; }
.signal-row small { color: var(--muted); overflow-wrap: anywhere; }
.score { align-self: start; font-variant-numeric: tabular-nums; font-weight: 700; color: var(--green); }
.row-meta { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.status-badge { display: inline-flex; align-items: center; min-height: 24px; padding: 2px 7px; border-radius: 999px;
  background: var(--amber-soft); color: var(--amber); font-size: 11px; font-weight: 700; text-transform: uppercase; }
.status-badge.confirmed, .status-badge.published, .status-badge.approve { background: var(--green-soft); color: var(--green); }
.status-badge.suppressed, .status-badge.expired, .status-badge.withdrawn, .status-badge.reject { background: var(--red-soft); color: var(--red); }
.status-badge.neutral { background: #eceff1; color: #56616a; }
.signal-metrics { margin: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--line); }
.signal-metrics div { padding: 12px 16px; border-right: 1px solid var(--line); }
.signal-metrics div:last-child { border-right: 0; }
dt { color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 700; }
dd { margin: 4px 0 0; overflow-wrap: anywhere; }
.decision-strip { padding: 14px 16px; background: #f7f8f8; border-bottom: 1px solid var(--line); }
.decision-strip > label { display: block; margin-bottom: 5px; color: var(--muted); font-size: 12px; font-weight: 700; }
.decision-strip textarea { width: 100%; resize: vertical; padding: 8px; }
.decision-actions { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 7px; }
.decision-actions .approve { background: var(--green); border-color: var(--green); color: #fff; }
.decision-actions .reject { background: var(--red); border-color: var(--red); color: #fff; }
.decision-actions .quiet { margin-left: auto; }
.section-heading { min-height: 48px; padding: 10px 16px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); }
.section-heading h3 { margin: 0; font-size: 14px; }
.section-heading span { color: var(--muted); font-size: 12px; }
.evidence-list { padding: 0 16px; }
.evidence-item { padding: 15px 0; border-bottom: 1px solid var(--line); }
.evidence-item header { display: flex; justify-content: space-between; gap: 12px; }
.evidence-item h4 { margin: 0; font-size: 14px; }
.evidence-item small { color: var(--muted); }
.evidence-item blockquote { margin: 10px 0 0; padding-left: 12px; border-left: 3px solid #68a78b; line-height: 1.55; }
.evidence-item a { color: var(--blue); }
.identifier-list { margin: 0; padding: 12px 16px; display: grid; grid-template-columns: auto 1fr; gap: 6px 10px; border-bottom: 1px solid var(--line); }
.identifier-list dd { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
.timeline { list-style: none; margin: 0; padding: 0 16px 28px; }
.timeline li { position: relative; padding: 14px 0 14px 17px; border-bottom: 1px solid var(--line); }
.timeline li::before { content: ""; position: absolute; left: 0; top: 19px; width: 7px; height: 7px; border-radius: 50%; background: #2a7a59; }
.timeline strong, .timeline span { display: block; overflow-wrap: anywhere; }
.timeline span { margin-top: 4px; color: var(--muted); font-size: 12px; line-height: 1.4; }
.toast { position: fixed; right: 16px; bottom: 16px; max-width: min(420px, calc(100vw - 32px)); padding: 10px 14px;
  background: #20252b; color: #fff; border-radius: 4px; box-shadow: 0 4px 18px rgba(0,0,0,.22); opacity: 0; pointer-events: none; transition: opacity .15s; }
.toast.visible { opacity: 1; }
dialog { width: min(420px, calc(100vw - 32px)); border: 1px solid #aeb6bd; border-radius: 6px; padding: 0; }
dialog::backdrop { background: rgba(20, 25, 29, .72); }
dialog form { padding: 24px; display: grid; gap: 14px; }
dialog h2 { margin: 0; font-size: 20px; }
dialog label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; font-weight: 700; }
dialog input { height: 38px; padding: 7px 9px; }
dialog button { background: var(--green); color: #fff; border-color: var(--green); }
#session-error { min-height: 18px; margin: 0; color: var(--red); font-size: 12px; }
.empty-state { padding: 30px 16px; color: var(--muted); text-align: center; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
@media (max-width: 1100px) {
  .workspace { grid-template-columns: 300px minmax(400px, 1fr); }
  .entity-panel { grid-column: 1 / -1; border-left: 0; border-top: 1px solid var(--line); }
  .timeline { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 18px; }
}
@media (max-width: 760px) {
  .topbar { height: auto; min-height: 64px; align-items: flex-start; gap: 12px; padding: 10px 12px; }
  .brand-block { min-width: 0; }
  .brand-block div span { display: none; }
  .toolbar { flex-wrap: wrap; justify-content: flex-end; }
  .toolbar label { flex: 1 1 110px; }
  .toolbar select { width: 100%; min-width: 0; }
  .workspace { min-height: 0; grid-template-columns: 1fr; }
  .inbox-panel, .entity-panel { border: 0; border-bottom: 1px solid var(--line); }
  .signal-list { max-height: 360px; }
  .signal-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .signal-metrics div:nth-child(2) { border-right: 0; }
  .signal-metrics div:nth-child(-n+2) { border-bottom: 1px solid var(--line); }
  .timeline { display: block; }
  .decision-actions .quiet { margin-left: 0; }
}
"""


WORKBENCH_JS = """(() => {
  "use strict";
  const state = { token: sessionStorage.getItem("fi_intel_token") || "", signals: [], selected: null };
  const byId = (id) => document.getElementById(id);
  const dialog = byId("session-dialog");
  const toast = byId("toast");

  function notify(message) {
    toast.textContent = message;
    toast.classList.add("visible");
    window.setTimeout(() => toast.classList.remove("visible"), 2600);
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${state.token}`);
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    if (response.status === 401 || response.status === 403) {
      sessionStorage.removeItem("fi_intel_token");
      state.token = "";
      if (!dialog.open) dialog.showModal();
    }
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try { detail = (await response.json()).detail || detail; } catch (_) { /* response is not JSON */ }
      throw new Error(detail);
    }
    return response.json();
  }

  const formatDate = (value) => value ? new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short"
  }).format(new Date(value)) : "-";

  function statusBadge(status) {
    const span = document.createElement("span");
    span.className = `status-badge ${status || "neutral"}`;
    span.textContent = (status || "unknown").replaceAll("_", " ");
    return span;
  }

  function renderSignals() {
    const query = byId("signal-search").value.trim().toLowerCase();
    const rows = state.signals.filter((signal) =>
      !query || signal.entity_name.toLowerCase().includes(query) ||
      signal.pattern_id.toLowerCase().includes(query)
    );
    const list = byId("signal-list");
    list.replaceChildren();
    byId("signal-count").textContent = String(rows.length);
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No signals match the current filters.";
      list.append(empty);
      return;
    }
    rows.forEach((signal) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `signal-row${state.selected === signal.signal_id ? " selected" : ""}`;
      button.dataset.signalId = signal.signal_id;
      const name = document.createElement("strong");
      name.textContent = signal.entity_name;
      const score = document.createElement("span");
      score.className = "score";
      score.textContent = signal.score == null ? "-" : signal.score.toFixed(2);
      const pattern = document.createElement("small");
      pattern.textContent = signal.pattern_id.replaceAll("_", " ");
      const meta = document.createElement("div");
      meta.className = "row-meta";
      meta.append(statusBadge(signal.status));
      const changed = document.createElement("small");
      changed.textContent = formatDate(signal.changed_at);
      meta.append(changed);
      button.append(name, score, pattern, meta);
      button.addEventListener("click", () => selectSignal(signal.signal_id));
      list.append(button);
    });
  }

  async function loadSignals() {
    const desk = byId("desk-select").value;
    if (!desk) return;
    const status = byId("status-filter").value;
    const params = new URLSearchParams({ desk, limit: "200" });
    if (status) params.set("status", status);
    state.signals = await api(`/v1/signals?${params}`);
    if (state.selected && !state.signals.some((item) => item.signal_id === state.selected)) {
      state.selected = null;
    }
    renderSignals();
    if (!state.selected && state.signals.length) await selectSignal(state.signals[0].signal_id);
  }

  function renderEvidence(items) {
    const list = byId("evidence-list");
    list.replaceChildren();
    byId("evidence-count").textContent = `${items.length} span${items.length === 1 ? "" : "s"}`;
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No authorized evidence spans are attached.";
      list.append(empty);
      return;
    }
    items.forEach((item) => {
      const article = document.createElement("article");
      article.className = "evidence-item";
      const header = document.createElement("header");
      const title = document.createElement("h4");
      title.textContent = item.title;
      const source = document.createElement("small");
      source.textContent = item.source_id || "source";
      header.append(title, source);
      const quote = document.createElement("blockquote");
      quote.textContent = item.quote;
      article.append(header, quote);
      if (item.source_url) {
        const link = document.createElement("a");
        link.href = item.source_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Open source";
        article.append(link);
      }
      list.append(article);
    });
  }

  function objectSummary(value) {
    if (!value || typeof value !== "object") return String(value ?? "");
    if (value.value != null) return String(value.value);
    if (value.entity_id) return `Entity ${value.entity_id}`;
    return JSON.stringify(value);
  }

  function renderEntity(entity) {
    byId("entity-heading").textContent = entity.canonical_name;
    const identifiers = byId("entity-identifiers");
    identifiers.replaceChildren();
    Object.entries(entity.identifiers).forEach(([scheme, value]) => {
      const term = document.createElement("dt");
      term.textContent = scheme.toUpperCase();
      const detail = document.createElement("dd");
      detail.textContent = value;
      identifiers.append(term, detail);
    });
    const timeline = byId("entity-timeline");
    timeline.replaceChildren();
    byId("timeline-count").textContent = `${entity.timeline.length} facts`;
    entity.timeline.forEach((fact) => {
      const item = document.createElement("li");
      const predicate = document.createElement("strong");
      predicate.textContent = fact.predicate.replaceAll("_", " ");
      const value = document.createElement("span");
      value.textContent = objectSummary(fact.object_json);
      const date = document.createElement("span");
      date.textContent = `${formatDate(fact.event_time || fact.valid_from)} · confidence ${fact.confidence.toFixed(2)}`;
      item.append(predicate, value, date);
      timeline.append(item);
    });
  }

  async function selectSignal(signalId) {
    state.selected = signalId;
    renderSignals();
    try {
      const signal = await api(`/v1/signals/${encodeURIComponent(signalId)}`);
      byId("detail-pattern").textContent = `${signal.pattern_id} · v${signal.pattern_version}`;
      byId("detail-heading").textContent = signal.entity_name;
      const badge = statusBadge(signal.status);
      badge.id = "detail-status";
      byId("detail-status").replaceWith(badge);
      byId("detail-score").textContent = signal.score == null ? "-" : signal.score.toFixed(3);
      byId("detail-as-of").textContent = formatDate(signal.as_of);
      byId("detail-changed").textContent = formatDate(signal.changed_at);
      byId("detail-feedback").textContent = signal.latest_feedback || "-";
      const [entity, evidence] = await Promise.all([
        api(`/v1/entities/${encodeURIComponent(signal.entity_id)}`),
        Promise.all(signal.evidence_span_ids.map((id) =>
          api(`/v1/evidence/${encodeURIComponent(id)}`)
        ))
      ]);
      renderEntity(entity);
      renderEvidence(evidence);
    } catch (error) {
      notify(error.message);
    }
  }

  async function submitFeedback(verdict) {
    if (!state.selected) return;
    const reason = byId("feedback-reason").value.trim();
    if (!reason) { notify("A decision note is required."); return; }
    try {
      await api(`/v1/signals/${encodeURIComponent(state.selected)}/feedback`, {
        method: "POST", body: JSON.stringify({ verdict, reason })
      });
      byId("feedback-reason").value = "";
      notify("Decision recorded.");
      await loadSignals();
    } catch (error) { notify(error.message); }
  }

  async function closeSignal() {
    if (!state.selected) return;
    const note = byId("feedback-reason").value.trim();
    if (!note) { notify("A close note is required."); return; }
    try {
      await api(`/v1/signals/${encodeURIComponent(state.selected)}/close`, {
        method: "POST",
        body: JSON.stringify({ reason: byId("close-reason").value, note })
      });
      byId("feedback-reason").value = "";
      notify("Signal closed.");
      await loadSignals();
    } catch (error) { notify(error.message); }
  }

  async function establishSession() {
    const session = await api("/v1/session");
    const deskSelect = byId("desk-select");
    deskSelect.replaceChildren();
    session.desks.forEach((desk) => {
      const option = document.createElement("option");
      option.value = desk;
      option.textContent = desk.replaceAll("_", " ");
      deskSelect.append(option);
    });
    if (!session.desks.length) throw new Error("No analyst desk is assigned.");
    dialog.close();
    await loadSignals();
  }

  byId("session-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    state.token = byId("token-input").value.trim();
    sessionStorage.setItem("fi_intel_token", state.token);
    try { await establishSession(); } catch (error) { byId("session-error").textContent = error.message; }
  });
  byId("logout-button").addEventListener("click", () => {
    sessionStorage.removeItem("fi_intel_token"); state.token = "";
    if (!dialog.open) dialog.showModal();
  });
  byId("refresh-button").addEventListener("click", () => loadSignals().catch((error) => notify(error.message)));
  byId("desk-select").addEventListener("change", () => { state.selected = null; loadSignals().catch((error) => notify(error.message)); });
  byId("status-filter").addEventListener("change", () => { state.selected = null; loadSignals().catch((error) => notify(error.message)); });
  byId("signal-search").addEventListener("input", renderSignals);
  document.querySelectorAll("[data-verdict]").forEach((button) =>
    button.addEventListener("click", () => submitFeedback(button.dataset.verdict))
  );
  byId("close-signal").addEventListener("click", closeSignal);

  if (state.token) establishSession().catch(() => { if (!dialog.open) dialog.showModal(); });
  else if (!dialog.open) dialog.showModal();
})();
"""
