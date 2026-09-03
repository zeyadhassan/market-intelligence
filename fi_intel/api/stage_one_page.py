# ruff: noqa: E501
"""Self-contained assets for the operations-first Stage One page."""

STAGE_ONE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>FI Intelligence Control Room</title>
  <link rel="stylesheet" href="/stage-one/assets/stage-one.css">
</head>
<body>
  <a class="skip-link" href="#pipeline">Skip to pipeline status</a>
  <header class="topbar">
    <a class="brand" href="/stage-one" aria-label="FI Intelligence control room home">
      <span class="brand-mark" aria-hidden="true">FI</span>
      <span><strong>Intelligence Control Room</strong><small>Local GCC intelligence</small></span>
    </a>
    <div class="runtime-head">
      <span id="runtime-dot" class="status-dot waiting" aria-hidden="true"></span>
      <span><strong id="runtime-status">Connecting</strong><small id="last-updated">Waiting for runtime data</small></span>
      <button id="toggle-live" class="button secondary" type="button">Pause live updates</button>
    </div>
  </header>

  <main>
    <section class="page-intro">
      <div>
        <p class="eyebrow">Official sources + configured models</p>
        <h1>See exactly what the system is doing.</h1>
        <p>Live worker state, source acquisition, indexing, model calls, agent steps, failures, and results in one place.</p>
      </div>
      <button id="refresh-dashboard" class="button primary" type="button">Refresh now</button>
    </section>

    <section class="metric-grid" aria-label="Runtime summary">
      <article class="metric-card"><span>Active work</span><strong id="active-work">—</strong><small>stages currently running</small></article>
      <article class="metric-card"><span>Pending work</span><strong id="pending-work">—</strong><small>queued or blocked items</small></article>
      <article class="metric-card"><span>Failures</span><strong id="failure-count">—</strong><small>current actionable failures</small></article>
      <article class="metric-card"><span>Knowledge base</span><strong id="knowledge-count">—</strong><small>indexed document versions</small></article>
    </section>

    <section id="pipeline" class="panel">
      <div class="panel-heading">
        <div><p class="eyebrow">End-to-end pipeline</p><h2>Processing stages</h2></div>
        <p>Working means the stage is active now. Waiting means it has not received ready input.</p>
      </div>
      <div id="pipeline-stages" class="stage-grid" aria-live="polite">
        <div class="loading">Loading durable pipeline state…</div>
      </div>
    </section>

    <div class="two-column">
      <section class="panel">
        <div class="panel-heading"><div><p class="eyebrow">Processes</p><h2>Workers</h2></div></div>
        <div class="table-scroll">
          <table><thead><tr><th>Worker</th><th>State</th><th>Current operation</th><th>Heartbeat</th></tr></thead><tbody id="workers-body"></tbody></table>
        </div>
      </section>
      <section class="panel">
        <div class="panel-heading"><div><p class="eyebrow">Inference</p><h2>Model activity</h2></div></div>
        <div class="table-scroll">
          <table><thead><tr><th>Role</th><th>State</th><th>Model</th><th>Calls / failures</th><th>Latency</th></tr></thead><tbody id="models-body"></tbody></table>
        </div>
      </section>
    </div>

    <section class="panel">
      <div class="panel-heading">
        <div><p class="eyebrow">Acquisition</p><h2>Live sources</h2></div>
        <p>Every configured source is shown, including sources that have never run.</p>
      </div>
      <div class="table-scroll">
        <table><thead><tr><th>Source</th><th>State</th><th>Last activity</th><th>Discovered</th><th>Committed</th><th>Quarantined</th><th>Detail</th></tr></thead><tbody id="sources-body"></tbody></table>
      </div>
    </section>

    <section class="panel event-panel">
      <div class="panel-heading">
        <div><p class="eyebrow">Debugging</p><h2>Activity log</h2></div>
        <div class="inline-actions"><span id="event-count" class="muted"></span><button id="copy-diagnostics" class="button secondary" type="button">Copy diagnostics</button></div>
      </div>
      <div class="filters">
        <label>Stage<select id="event-stage"><option value="">All stages</option></select></label>
        <label>Status<select id="event-status"><option value="">All statuses</option><option value="failed">Failures only</option><option value="working">Working only</option><option value="succeeded">Succeeded only</option></select></label>
        <label class="filter-grow">Find<input id="event-search" type="search" placeholder="operation, message, run ID"></label>
      </div>
      <div id="event-list" class="event-list" aria-live="polite"></div>
      <p class="safe-note">This log intentionally excludes prompts, credentials, private document text, and raw model payloads.</p>
    </section>

    <section class="panel research-panel">
      <div class="panel-heading">
        <div><p class="eyebrow">On-demand</p><h2>Ask the knowledge base</h2></div>
        <p>Runs governed hybrid retrieval and model reasoning over indexed evidence.</p>
      </div>
      <form id="research-form" class="research-form">
        <label for="research-query">Research question</label>
        <div class="research-input-row"><textarea id="research-query" rows="3" maxlength="2000" required>What upcoming funding needs or refinancing gaps are visible in the current indexed evidence?</textarea><button id="research-submit" class="button primary" type="submit">Run research</button></div>
      </form>
      <div id="research-state" class="notice neutral" role="status">No research request is running.</div>
      <div id="research-answer" class="research-answer"></div>
    </section>

    <section class="panel topic-panel">
      <div class="panel-heading">
        <div><p class="eyebrow">Scheduled intelligence</p><h2>Choose what you want to follow</h2></div>
        <button id="refresh-button" class="button primary" type="button" disabled>Run selected topic</button>
      </div>
      <div id="topic-list" class="topic-list"><div class="loading">Loading topics…</div></div>
      <div id="analysis-state" class="notice neutral" role="status" aria-live="polite">Choose a topic to inspect its latest analysis.</div>
      <div id="coverage-ledger" class="coverage-ledger" hidden></div>
      <div class="results-heading"><h3>Today's opportunities</h3></div>
      <div id="result-list" class="result-list"></div>
    </section>
  </main>

  <div id="toast" class="toast" role="status" aria-live="polite"></div>
  <script src="/stage-one/assets/stage-one.js" defer></script>
</body>
</html>
"""

STAGE_ONE_FIXTURE_HTML = (
    STAGE_ONE_HTML.replace("Local GCC intelligence", "Synthetic fixture")
    .replace("Official sources + configured models", "No network or LLM calls")
    .replace(
        "Live worker state, source acquisition, indexing, model calls, agent steps, failures, and results in one place.",
        "Fixture product behavior without live workers, network sources, or model gateways.",
    )
)


STAGE_ONE_CSS = """:root {
  color-scheme: light;
  --bg: #f4f5f7;
  --surface: #ffffff;
  --surface-2: #f8f9fb;
  --ink: #17191c;
  --muted: #626a73;
  --line: #d9dde3;
  --line-dark: #aeb5bf;
  --accent: #165dff;
  --accent-dark: #0d43ba;
  --success: #137333;
  --success-bg: #e8f5ec;
  --warning: #8a4b08;
  --warning-bg: #fff3df;
  --danger: #b3261e;
  --danger-bg: #fcebea;
  --working: #0759c7;
  --working-bg: #e8f0ff;
  --offline: #5f6368;
  --offline-bg: #eceff3;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; background: var(--bg); color: var(--ink); }
button, input, textarea, select { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .55; }
button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible, a:focus-visible, summary:focus-visible { outline: 3px solid rgba(22,93,255,.25); outline-offset: 2px; }
.skip-link { position: fixed; z-index: 20; top: -60px; left: 12px; padding: 9px 12px; background: var(--surface); color: var(--ink); }
.skip-link:focus { top: 12px; }
.topbar { position: sticky; top: 0; z-index: 10; min-height: 68px; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 10px max(20px, calc((100vw - 1440px) / 2)); background: rgba(255,255,255,.97); border-bottom: 1px solid var(--line); }
.brand { display: flex; align-items: center; gap: 11px; color: var(--ink); text-decoration: none; }
.brand-mark { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 6px; background: var(--ink); color: #fff; font-weight: 800; letter-spacing: -.04em; }
.brand strong, .brand small, .runtime-head strong, .runtime-head small { display: block; }
.brand small, .runtime-head small { margin-top: 2px; color: var(--muted); font-size: 11px; }
.runtime-head { display: flex; align-items: center; gap: 9px; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--offline); box-shadow: 0 0 0 4px var(--offline-bg); }
.status-dot.healthy, .status-dot.complete, .status-dot.succeeded, .status-dot.idle { background: var(--success); box-shadow: 0 0 0 4px var(--success-bg); }
.status-dot.working, .status-dot.started { background: var(--working); box-shadow: 0 0 0 4px var(--working-bg); animation: pulse 1.4s infinite; }
.status-dot.failed { background: var(--danger); box-shadow: 0 0 0 4px var(--danger-bg); }
.status-dot.attention, .status-dot.waiting { background: var(--warning); box-shadow: 0 0 0 4px var(--warning-bg); }
@keyframes pulse { 50% { opacity: .38; } }
main { width: min(1440px, calc(100% - 32px)); margin: 0 auto; padding: 30px 0 64px; }
.page-intro { display: flex; justify-content: space-between; align-items: end; gap: 20px; margin-bottom: 18px; }
.page-intro h1 { margin: 3px 0 7px; font-size: clamp(27px, 4vw, 42px); line-height: 1.05; letter-spacing: -.035em; }
.page-intro p { margin: 0; max-width: 820px; color: var(--muted); line-height: 1.5; }
.eyebrow { margin: 0 0 4px !important; color: var(--accent) !important; font-size: 11px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.button { min-height: 36px; padding: 7px 12px; border: 1px solid transparent; border-radius: 6px; font-weight: 700; white-space: nowrap; }
.button.primary { background: var(--accent); color: #fff; }
.button.primary:hover { background: var(--accent-dark); }
.button.secondary { border-color: var(--line-dark); background: var(--surface); color: var(--ink); }
.button.secondary:hover { background: var(--surface-2); }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 10px; margin-bottom: 10px; }
.metric-card { padding: 15px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
.metric-card span, .metric-card small { display: block; color: var(--muted); font-size: 12px; }
.metric-card strong { display: block; margin: 5px 0 3px; font-size: 28px; letter-spacing: -.03em; }
.panel { margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); overflow: hidden; }
.panel-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 62px; padding: 13px 16px; border-bottom: 1px solid var(--line); }
.panel-heading h2 { margin: 0; font-size: 18px; }
.panel-heading p { margin: 0; max-width: 680px; color: var(--muted); font-size: 12px; line-height: 1.45; }
.stage-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 1px; background: var(--line); }
.stage-card { position: relative; min-height: 178px; padding: 16px; background: var(--surface); }
.stage-top { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
.stage-card h3 { margin: 0; font-size: 14px; line-height: 1.35; }
.stage-card p { min-height: 40px; margin: 11px 0; color: var(--muted); font-size: 12px; line-height: 1.45; }
.stage-counts { display: flex; flex-wrap: wrap; gap: 6px 12px; color: var(--muted); font-size: 11px; }
.stage-time { display: block; margin-top: 12px; color: var(--muted); font-size: 10px; }
.badge { display: inline-flex; align-items: center; min-height: 23px; padding: 3px 8px; border-radius: 999px; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .035em; }
.badge.complete, .badge.healthy, .badge.succeeded, .badge.idle { color: var(--success); background: var(--success-bg); }
.badge.working, .badge.started { color: var(--working); background: var(--working-bg); }
.badge.waiting, .badge.attention, .badge.refused, .badge.malformed { color: var(--warning); background: var(--warning-bg); }
.badge.failed, .badge.timed_out, .badge.offline, .badge.stopped { color: var(--danger); background: var(--danger-bg); }
.badge.not_started { color: var(--offline); background: var(--offline-bg); }
.two-column { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { padding: 9px 12px; background: var(--surface-2); border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: left; text-transform: uppercase; letter-spacing: .05em; }
td { padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; line-height: 1.4; }
tbody tr:last-child td { border-bottom: 0; }
td strong, td small { display: block; }
td small { margin-top: 2px; color: var(--muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 10px; word-break: break-all; }
.muted { color: var(--muted); font-size: 12px; }
.inline-actions { display: flex; align-items: center; gap: 9px; }
.filters { display: grid; grid-template-columns: 170px 170px minmax(220px,1fr); gap: 10px; padding: 12px 16px; border-bottom: 1px solid var(--line); background: var(--surface-2); }
.filters label, .research-form label { color: var(--muted); font-size: 11px; font-weight: 700; }
.filters select, .filters input, textarea { width: 100%; margin-top: 4px; border: 1px solid var(--line-dark); border-radius: 5px; background: #fff; color: var(--ink); }
.filters select, .filters input { height: 35px; padding: 6px 8px; }
.event-list { max-height: 560px; overflow: auto; }
.event-row { display: grid; grid-template-columns: 110px 110px 160px 1fr; gap: 10px; padding: 9px 16px; border-bottom: 1px solid var(--line); align-items: start; font-size: 12px; }
.event-row:hover { background: var(--surface-2); }
.event-row time { color: var(--muted); font-variant-numeric: tabular-nums; }
.event-message strong { display: block; margin-bottom: 2px; }
.event-message small { display: block; color: var(--muted); word-break: break-word; }
.safe-note { margin: 0; padding: 9px 16px; background: var(--surface-2); color: var(--muted); font-size: 10px; }
.research-panel, .topic-panel { padding-bottom: 16px; }
.research-form { padding: 14px 16px 0; }
.research-input-row { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: stretch; }
textarea { min-height: 80px; padding: 10px; resize: vertical; line-height: 1.45; }
.research-input-row .button { min-width: 130px; }
.notice { margin: 14px 16px 0; padding: 11px 12px; border: 1px solid var(--line); border-left-width: 4px; border-radius: 5px; font-size: 12px; line-height: 1.45; }
.notice.neutral { background: var(--surface-2); }
.notice.working, .notice.queued, .notice.running, .notice.deferred { border-left-color: var(--working); background: var(--working-bg); }
.notice.complete { border-left-color: var(--success); background: var(--success-bg); }
.notice.failed, .notice.retryable_failed, .notice.terminal_failed { border-left-color: var(--danger); background: var(--danger-bg); }
.notice.held, .notice.partial { border-left-color: var(--warning); background: var(--warning-bg); }
.research-answer { padding: 0 16px; }
.research-answer h3 { margin-bottom: 5px; }
.research-answer p, .research-answer li { font-size: 13px; line-height: 1.5; }
.topic-list { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; padding: 14px 16px; }
.topic-item { display: flex; justify-content: space-between; gap: 14px; padding: 13px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface-2); }
.topic-item.selected { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); background: #f2f6ff; }
.topic-copy strong, .topic-copy small { display: block; }
.topic-copy small { margin-top: 4px; color: var(--muted); line-height: 1.4; }
.topic-actions { display: flex; flex-direction: column; gap: 6px; align-items: stretch; }
.topic-actions button { min-height: 30px; padding: 4px 8px; }
.coverage-ledger { margin: 10px 16px 0; padding: 10px 12px; border: 1px solid var(--line); border-radius: 5px; font-size: 12px; }
.coverage-ledger strong, .coverage-ledger small { display: block; }
.coverage-ledger small { margin-top: 3px; color: var(--muted); }
.results-heading { padding: 16px 16px 0; }
.results-heading h3 { margin: 0; font-size: 15px; }
.result-list { display: grid; gap: 8px; padding: 10px 16px 0; }
.result-card { border: 1px solid var(--line); border-radius: 6px; padding: 14px; }
.result-card h3 { margin: 0 0 5px; font-size: 16px; }
.result-card p { margin: 7px 0; font-size: 13px; line-height: 1.5; }
.result-meta { display: flex; flex-wrap: wrap; gap: 6px 12px; color: var(--muted); font-size: 11px; }
.result-facts { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; margin-top: 11px; }
.result-fact { padding: 10px; border: 1px solid var(--line); border-radius: 5px; background: var(--surface-2); }
.result-fact small { display: block; color: var(--muted); font-size: 10px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase; }
.result-fact p { margin: 4px 0 0; font-size: 12px; }
.analysis-detail { margin-top: 10px; border-top: 1px solid var(--line); padding-top: 9px; }
.analysis-detail summary { cursor: pointer; font-size: 12px; font-weight: 800; }
.analysis-detail dl { display: grid; grid-template-columns: 150px 1fr; gap: 6px 12px; margin: 10px 0 0; font-size: 12px; }
.analysis-detail dt { color: var(--muted); font-weight: 700; }
.analysis-detail dd { margin: 0; line-height: 1.45; word-break: break-word; }
.evidence-list { margin-top: 10px; }
.evidence-list details { border-top: 1px solid var(--line); padding: 8px 0; }
.evidence-list summary { cursor: pointer; font-size: 12px; font-weight: 700; }
.evidence-list blockquote { margin: 8px 0; padding-left: 10px; border-left: 3px solid var(--line-dark); color: var(--muted); font-size: 12px; line-height: 1.45; }
.evidence-meta { color: var(--muted); font-size: 10px; line-height: 1.45; }
.evidence-link { display: inline-block; margin-top: 5px; color: var(--accent-dark); font-size: 11px; font-weight: 700; }
.evaluation-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.evaluation-row button { min-height: 29px; padding: 4px 8px; border: 1px solid var(--line-dark); border-radius: 5px; background: #fff; font-size: 11px; }
.loading, .empty { padding: 20px; color: var(--muted); font-size: 12px; }
.toast { position: fixed; z-index: 30; right: 18px; bottom: 18px; max-width: min(420px,calc(100vw - 36px)); padding: 10px 13px; border-radius: 6px; background: var(--ink); color: #fff; font-size: 12px; opacity: 0; transform: translateY(8px); pointer-events: none; transition: .18s ease; }
.toast.visible { opacity: 1; transform: translateY(0); }
@media (max-width: 1100px) { .stage-grid { grid-template-columns: repeat(2,minmax(0,1fr)); } .two-column { grid-template-columns: 1fr; } }
@media (max-width: 720px) { .topbar { position: static; align-items: flex-start; } .runtime-head { flex-wrap: wrap; justify-content: flex-end; } .runtime-head > span:nth-child(2) { display: none; } main { width: min(100% - 20px,1440px); padding-top: 20px; } .page-intro { align-items: flex-start; } .metric-grid { grid-template-columns: repeat(2,minmax(0,1fr)); } .stage-grid, .topic-list, .result-facts { grid-template-columns: 1fr; } .filters { grid-template-columns: 1fr 1fr; } .filter-grow { grid-column: 1 / -1; } .event-row { grid-template-columns: 90px 95px 1fr; } .event-message { grid-column: 1 / -1; } .research-input-row { grid-template-columns: 1fr; } .analysis-detail dl { grid-template-columns: 1fr; gap: 2px; } .analysis-detail dd { margin-bottom: 6px; } }
@media (max-width: 480px) { .brand small { display: none; } .metric-grid, .filters { grid-template-columns: 1fr; } .filter-grow { grid-column: auto; } .page-intro { display: block; } .page-intro .button { margin-top: 12px; width: 100%; } .topic-item { display: block; } .topic-actions { flex-direction: row; margin-top: 10px; } }
"""


_STAGE_ONE_JS_TEMPLATE = r"""(() => {
  "use strict";
__FI_INTEL_TOKEN_PROVIDER__
  const byId = (id) => document.getElementById(id);
  const terminalAnalysis = new Set(["complete", "partial", "held", "terminal_failed"]);
  const terminalSearch = new Set(["complete", "held", "terminal_failed"]);
  const allowedStatuses = new Set(["healthy", "complete", "succeeded", "idle", "working", "started", "waiting", "attention", "failed", "timed_out", "offline", "stopped", "not_started", "refused", "malformed", "queued", "running", "deferred", "retryable_failed", "terminal_failed", "held", "partial"]);
  const state = {
    dashboard: null,
    live: true,
    dashboardTimer: null,
    topics: [],
    selectedTopic: null,
    analysisTimer: null,
    searchTimer: null,
    searchStarted: false
  };

  function statusClass(value) {
    const normalized = String(value || "waiting").toLowerCase().replaceAll(" ", "_");
    return allowedStatuses.has(normalized) ? normalized : "waiting";
  }

  function formatTime(value, withDate = false) {
    if (!value) return "never";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, withDate
      ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }
      : { hour: "2-digit", minute: "2-digit", second: "2-digit" }
    ).format(date);
  }

  function shortId(value) {
    if (!value) return "—";
    const text = String(value);
    return text.length > 18 ? `${text.slice(0, 8)}…${text.slice(-6)}` : text;
  }

  function badge(status) {
    const node = document.createElement("span");
    node.className = `badge ${statusClass(status)}`;
    node.textContent = String(status || "unknown").replaceAll("_", " ");
    return node;
  }

  function cell(value, secondary = null) {
    const td = document.createElement("td");
    const primary = document.createElement("strong");
    primary.textContent = value;
    td.append(primary);
    if (secondary) {
      const small = document.createElement("small");
      small.textContent = secondary;
      td.append(small);
    }
    return td;
  }

  function statusCell(status) {
    const td = document.createElement("td");
    td.append(badge(status));
    return td;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${bearerToken()}`);
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok && response.status !== 202) {
      throw new Error(payload?.detail || `Request failed (${response.status})`);
    }
    return payload;
  }

  let toastTimer = null;
  function notify(message) {
    const toast = byId("toast");
    toast.textContent = message;
    toast.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("visible"), 3200);
  }

  function renderRuntimeHead(dashboard) {
    const status = statusClass(dashboard.overall_status);
    byId("runtime-status").textContent = `Runtime ${dashboard.overall_status.replaceAll("_", " ")}`;
    byId("runtime-dot").className = `status-dot ${status}`;
    byId("last-updated").textContent = `Updated ${formatTime(dashboard.generated_at)}`;
    const active = dashboard.stages.filter((item) => item.status === "working").length;
    const pending = dashboard.stages.reduce((total, item) => total + item.pending, 0);
    const failures = dashboard.stages.reduce((total, item) => total + item.failed, 0) + dashboard.queue.dead_letters;
    byId("active-work").textContent = String(active);
    byId("pending-work").textContent = String(pending + dashboard.queue.outbox_pending);
    byId("failure-count").textContent = String(failures);
    byId("knowledge-count").textContent = String(dashboard.queue.indexed_document_versions);
  }

  function renderStages(stages) {
    const container = byId("pipeline-stages");
    container.replaceChildren();
    stages.forEach((item) => {
      const card = document.createElement("article");
      card.className = "stage-card";
      const top = document.createElement("div");
      top.className = "stage-top";
      const title = document.createElement("h3");
      title.textContent = item.label;
      top.append(title, badge(item.status));
      const detail = document.createElement("p");
      detail.textContent = item.detail;
      const counts = document.createElement("div");
      counts.className = "stage-counts";
      [["active", item.active], ["pending", item.pending], ["done", item.completed], ["failed", item.failed]].forEach(([label, value]) => {
        const span = document.createElement("span");
        span.textContent = `${label}: ${value}`;
        counts.append(span);
      });
      const time = document.createElement("span");
      time.className = "stage-time";
      time.textContent = `Last activity: ${formatTime(item.last_activity_at, true)}`;
      card.append(top, detail, counts, time);
      container.append(card);
    });
  }

  function renderWorkers(workers) {
    const body = byId("workers-body");
    body.replaceChildren();
    workers.forEach((item) => {
      const row = document.createElement("tr");
      row.append(
        cell(item.worker_type, shortId(item.worker_id)),
        statusCell(item.status),
        cell(item.operation, item.safe_error_summary),
        cell(formatTime(item.heartbeat_at), item.last_failure_at ? `last failure ${formatTime(item.last_failure_at, true)}` : null)
      );
      body.append(row);
    });
  }

  function renderModels(models) {
    const body = byId("models-body");
    body.replaceChildren();
    models.forEach((item) => {
      const row = document.createElement("tr");
      const latency = item.average_latency_ms == null ? "—" : `${(item.average_latency_ms / 1000).toFixed(1)}s`;
      row.append(
        cell(item.component, item.active_calls ? `${item.active_calls} active call(s)` : null),
        statusCell(item.status),
        cell(item.model, item.last_outcome ? `last: ${item.last_outcome}` : "no completed calls"),
        cell(`${item.calls_last_hour} / ${item.failed_last_hour}`, `${item.input_tokens_last_hour + item.output_tokens_last_hour} tokens`),
        cell(latency, formatTime(item.last_call_at))
      );
      body.append(row);
    });
  }

  function renderSources(sources) {
    const body = byId("sources-body");
    body.replaceChildren();
    if (!sources.length) {
      const row = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 7;
      td.textContent = "No live sources are configured in this mode.";
      row.append(td);
      body.append(row);
      return;
    }
    sources.forEach((item) => {
      const row = document.createElement("tr");
      row.append(
        cell(item.display_name, `${item.country} · ${item.source_id}`),
        statusCell(item.status),
        cell(formatTime(item.finished_at || item.started_at, true), item.run_id ? `run ${shortId(item.run_id)}` : null),
        cell(String(item.discovered), `${item.acquired} acquired`),
        cell(String(item.committed), `${item.unchanged} unchanged`),
        cell(String(item.quarantined)),
        cell(item.detail)
      );
      body.append(row);
    });
  }

  function populateStageFilter(events) {
    const select = byId("event-stage");
    const selected = select.value;
    const stages = [...new Set(events.map((item) => item.stage))].sort();
    select.replaceChildren(new Option("All stages", ""));
    stages.forEach((stage) => select.append(new Option(stage.replaceAll("_", " "), stage)));
    if (stages.includes(selected)) select.value = selected;
  }

  function renderEvents() {
    const dashboard = state.dashboard;
    if (!dashboard) return;
    const stage = byId("event-stage").value;
    const status = byId("event-status").value;
    const query = byId("event-search").value.trim().toLowerCase();
    const events = dashboard.events.filter((item) => {
      if (stage && item.stage !== stage) return false;
      if (status === "failed" && !["failed", "timed_out", "malformed", "refused"].includes(item.status)) return false;
      if (status === "working" && !["started", "working"].includes(item.status)) return false;
      if (status === "succeeded" && !["succeeded", "complete"].includes(item.status)) return false;
      if (query && !`${item.operation} ${item.message} ${item.run_id || ""} ${item.safe_error_summary || ""}`.toLowerCase().includes(query)) return false;
      return true;
    });
    byId("event-count").textContent = `${events.length} of ${dashboard.events.length} events`;
    const list = byId("event-list");
    list.replaceChildren();
    if (!events.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No events match these filters.";
      list.append(empty);
      return;
    }
    events.forEach((item) => {
      const row = document.createElement("div");
      row.className = "event-row";
      const time = document.createElement("time");
      time.dateTime = item.occurred_at;
      time.textContent = formatTime(item.occurred_at);
      const stageNode = document.createElement("span");
      stageNode.textContent = item.stage.replaceAll("_", " ");
      const statusNode = document.createElement("span");
      statusNode.append(badge(item.status));
      const message = document.createElement("div");
      message.className = "event-message";
      const operation = document.createElement("strong");
      operation.textContent = `${item.operation}: ${item.message}`;
      message.append(operation);
      const metadata = [item.run_id ? `run ${shortId(item.run_id)}` : null, item.worker_id ? `worker ${shortId(item.worker_id)}` : null, item.duration_ms == null ? null : `${Math.round(item.duration_ms)} ms`, item.safe_error_summary].filter(Boolean).join(" · ");
      if (metadata) {
        const small = document.createElement("small");
        small.textContent = metadata;
        message.append(small);
      }
      row.append(time, stageNode, statusNode, message);
      list.append(row);
    });
  }

  async function loadDashboard(showError = true) {
    try {
      const dashboard = await api("/v1/operations/dashboard?event_limit=200");
      state.dashboard = dashboard;
      renderRuntimeHead(dashboard);
      renderStages(dashboard.stages);
      renderWorkers(dashboard.workers);
      renderModels(dashboard.models);
      renderSources(dashboard.sources);
      populateStageFilter(dashboard.events);
      renderEvents();
      const searchStage = (dashboard.stages || []).find((item) => item.stage === "search");
      const recoveredIndexFailure = searchStage?.detail?.includes("RetrievalIndexNotReadyError");
      if (!state.searchStarted && recoveredIndexFailure && dashboard.queue?.retrieval_index_status === "ready") {
        setResearchNotice("attention", "A previous research job failed before the current ready index was available. Click Run research to create a fresh job.");
      }
    } catch (error) {
      byId("runtime-status").textContent = "Dashboard unavailable";
      byId("runtime-dot").className = "status-dot failed";
      byId("last-updated").textContent = error.message;
      if (showError) notify(error.message);
    }
  }

  function scheduleDashboard() {
    clearInterval(state.dashboardTimer);
    if (!state.live) return;
    state.dashboardTimer = setInterval(() => loadDashboard(false), 2000);
  }

  async function copyDiagnostics() {
    if (!state.dashboard) return;
    const dashboard = state.dashboard;
    const payload = {
      generated_at: dashboard.generated_at,
      overall_status: dashboard.overall_status,
      queue: dashboard.queue,
      workers: dashboard.workers,
      stages: dashboard.stages,
      sources: dashboard.sources,
      models: dashboard.models,
      failures: dashboard.events.filter((item) => ["failed", "timed_out", "malformed", "refused"].includes(item.status)).slice(0, 50)
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      notify("Safe diagnostics copied.");
    } catch (_) {
      notify("Clipboard access was unavailable.");
    }
  }

  function renderTopics() {
    const list = byId("topic-list");
    list.replaceChildren();
    state.topics.forEach((topic) => {
      const item = document.createElement("article");
      item.className = `topic-item${state.selectedTopic === topic.topic_id ? " selected" : ""}`;
      const copy = document.createElement("div");
      copy.className = "topic-copy";
      const title = document.createElement("strong");
      title.textContent = topic.label;
      const description = document.createElement("small");
      description.textContent = topic.description;
      copy.append(title, description);
      const actions = document.createElement("div");
      actions.className = "topic-actions";
      const follow = document.createElement("button");
      follow.type = "button";
      follow.className = "button secondary";
      follow.textContent = topic.subscribed ? "Unfollow" : "Follow";
      follow.addEventListener("click", () => toggleTopic(topic.topic_id, !topic.subscribed));
      const inspect = document.createElement("button");
      inspect.type = "button";
      inspect.className = "button secondary";
      inspect.textContent = "Inspect";
      inspect.disabled = !topic.subscribed;
      inspect.addEventListener("click", () => selectTopic(topic.topic_id, false));
      actions.append(follow, inspect);
      item.append(copy, actions);
      list.append(item);
    });
  }

  async function toggleTopic(topicId, active) {
    try {
      await api(`/v1/topics/${encodeURIComponent(topicId)}/subscription`, { method: "PUT", body: JSON.stringify({ active }) });
      const topic = state.topics.find((item) => item.topic_id === topicId);
      if (topic) topic.subscribed = active;
      renderTopics();
      if (active) await selectTopic(topicId, false);
    } catch (error) { notify(error.message); }
  }

  function setAnalysisNotice(kind, message) {
    const node = byId("analysis-state");
    node.className = `notice ${statusClass(kind)}`;
    node.textContent = message;
  }

  async function selectTopic(topicId, force) {
    state.selectedTopic = topicId;
    renderTopics();
    byId("refresh-button").disabled = false;
    clearTimeout(state.analysisTimer);
    setAnalysisNotice("working", force ? "Requested a fresh analysis job…" : "Loading the latest analysis…");
    try {
      const query = force ? "?refresh=true" : "";
      const resultSet = await api(`/v1/topics/${encodeURIComponent(topicId)}/results${query}`);
      if (state.selectedTopic !== topicId) return;
      renderTopicResults(resultSet);
      if (!terminalAnalysis.has(resultSet.analysis_status)) {
        state.analysisTimer = setTimeout(() => selectTopic(topicId, false), 2000);
      }
    } catch (error) {
      setAnalysisNotice("failed", error.message);
      notify(error.message);
    }
  }

  function renderTopicResults(resultSet) {
    setAnalysisNotice(resultSet.analysis_status, `${resultSet.message} · ${resultSet.scope_notice}`);
    const ledger = byId("coverage-ledger");
    ledger.hidden = false;
    ledger.replaceChildren();
    const headline = document.createElement("strong");
    headline.textContent = `Sources ${resultSet.successful_source_count}/${resultSet.required_source_count} · analysis ${resultSet.analysis_status}`;
    const model = document.createElement("small");
    model.textContent = resultSet.model_call_count
      ? `Model ${resultSet.model_name || "configured"}: ${resultSet.model_call_count} calls, ${resultSet.model_failure_count} failures`
      : (resultSet.coverage_state === "incomplete" ? "Model not invoked (coverage incomplete)" : "No analysis model call recorded yet");
    ledger.append(headline, model);
    console.table(resultSet.source_statuses || []);
    (resultSet.source_statuses || []).filter((item) => item.status !== "complete").forEach((item) => console.error(`[FI Intel] source ${item.source_id} ${item.status}: ${item.detail}`));
    if ((resultSet.source_statuses || []).some((item) => item.status !== "complete")) console.info("[FI Intel] backend logs: python deploy/podman_infra.py logs --no-follow --tail 500");
    const list = byId("result-list");
    list.replaceChildren();
    if (!resultSet.results.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = terminalAnalysis.has(resultSet.analysis_status)
        ? "No supported opportunity was published for this topic."
        : "The pipeline is still working. Follow its exact stage above.";
      list.append(empty);
      return;
    }
    resultSet.results.forEach((result) => list.append(resultCard(result)));
  }

  function resultCard(result) {
    const card = document.createElement("article");
    card.className = "result-card";
    const title = document.createElement("h3");
    title.textContent = result.title;
    const meta = document.createElement("div");
    meta.className = "result-meta";
    meta.textContent = `${result.entity_name} · score ${Number(result.score).toFixed(2)} · ${result.lifecycle_state} · ${formatTime(result.changed_at, true)}`;
    const summary = document.createElement("p");
    summary.textContent = result.summary;
    const facts = document.createElement("div");
    facts.className = "result-facts";
    [["Why now", result.why_now], ["Commercial angle", result.commercial_angle], ["Materiality", result.materiality]].filter(([, value]) => value).forEach(([label, value]) => {
      const fact = document.createElement("div");
      fact.className = "result-fact";
      const factLabel = document.createElement("small");
      factLabel.textContent = label;
      const factValue = document.createElement("p");
      factValue.textContent = value;
      fact.append(factLabel, factValue);
      facts.append(fact);
    });
    const analysis = document.createElement("details");
    analysis.className = "analysis-detail";
    const analysisSummary = document.createElement("summary");
    analysisSummary.textContent = "Analysis, uncertainty, and investigation trace";
    const analysisList = document.createElement("dl");
    const detailRows = [
      ["Freshness", result.freshness_reason],
      ["Coverage", result.coverage_details || result.coverage_state],
      ["Uncertainty", result.uncertainty],
      ["Falsifier", result.falsifier],
      ["Change", result.change_summary],
      ["Contradictions", (result.contradictions || []).join(" | ")],
      ["Agent trace", (result.investigation_trace || []).map((item) => JSON.stringify(item)).join(" | ")]
    ];
    detailRows.filter(([, value]) => value).forEach(([label, value]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const description = document.createElement("dd");
      description.textContent = value;
      analysisList.append(term, description);
    });
    analysis.append(analysisSummary, analysisList);
    const evidenceList = document.createElement("div");
    evidenceList.className = "evidence-list";
    (result.evidence || []).forEach((evidence) => {
      const detail = document.createElement("details");
      detail.className = "source-detail";
      const heading = document.createElement("summary");
      heading.textContent = `${evidence.source_id} · ${evidence.title}`;
      const quote = document.createElement("blockquote");
      quote.textContent = evidence.quote;
      const evidenceMeta = document.createElement("div");
      evidenceMeta.className = "evidence-meta";
      evidenceMeta.textContent = [evidence.country, evidence.source_type, evidence.published_at ? `published ${formatTime(evidence.published_at, true)}` : null, evidence.fetched_at ? `fetched ${formatTime(evidence.fetched_at, true)}` : null, evidence.content_hash ? `content ${shortId(evidence.content_hash)}` : null].filter(Boolean).join(" · ");
      detail.append(heading, quote, evidenceMeta);
      if (evidence.source_url && /^https?:\/\//i.test(evidence.source_url)) {
        const sourceLink = document.createElement("a");
        sourceLink.className = "evidence-link";
        sourceLink.href = evidence.source_url;
        sourceLink.target = "_blank";
        sourceLink.rel = "noreferrer noopener";
        sourceLink.textContent = "Open official source";
        detail.append(sourceLink);
      }
      evidenceList.append(detail);
    });
    const evaluations = document.createElement("div");
    evaluations.className = "evaluation-row";
    [["useful", "Useful"], ["not_relevant", "Not relevant"], ["incorrect", "Incorrect"], ["too_old", "Too old"]].forEach(([verdict, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", async () => {
        try {
          await api(`/v1/results/${encodeURIComponent(result.result_id)}/evaluation`, { method: "POST", body: JSON.stringify({ verdict, note: "" }) });
          notify(`Recorded: ${label}`);
        } catch (error) { notify(error.message); }
      });
      evaluations.append(button);
    });
    card.append(title, meta, summary, facts, analysis, evidenceList, evaluations);
    return card;
  }

  async function loadTopics() {
    try {
      state.topics = await api("/v1/topics");
      renderTopics();
      const first = state.topics.find((item) => item.subscribed);
      if (first) await selectTopic(first.topic_id, false);
    } catch (error) {
      byId("topic-list").textContent = error.message;
      notify(error.message);
    }
  }

  function setResearchNotice(kind, message) {
    const node = byId("research-state");
    node.className = `notice ${statusClass(kind)}`;
    node.textContent = message;
  }

  async function runResearch(event) {
    event.preventDefault();
    const query = byId("research-query").value.trim();
    if (!query) {
      setResearchNotice("attention", "Enter a research question before running the search.");
      return;
    }
    state.searchStarted = true;
    clearTimeout(state.searchTimer);
    byId("research-submit").disabled = true;
    byId("research-answer").replaceChildren();
    setResearchNotice("working", "Creating a durable research job…");
    try {
      const search = await api("/v1/searches", { method: "POST", body: JSON.stringify({ query, seed_entity_ids: [] }) });
      await pollResearch(search.search_id);
    } catch (error) {
      byId("research-submit").disabled = false;
      setResearchNotice("failed", error.message);
    }
  }

  async function pollResearch(searchId) {
    try {
      const search = await api(`/v1/searches/${encodeURIComponent(searchId)}`);
      setResearchNotice(search.state, `Research ${search.state} · route ${search.route} · job ${shortId(search.search_id)}` + (search.safe_error_summary ? ` · ${search.safe_error_summary}` : ""));
      if (terminalSearch.has(search.state)) {
        byId("research-submit").disabled = false;
        renderResearchAnswer(search.answer);
        return;
      }
      state.searchTimer = setTimeout(() => pollResearch(searchId), 2000);
    } catch (error) {
      byId("research-submit").disabled = false;
      setResearchNotice("failed", error.message);
    }
  }

  function renderResearchAnswer(answer) {
    const container = byId("research-answer");
    container.replaceChildren();
    if (!answer) return;
    const title = document.createElement("h3");
    title.textContent = answer.title || answer.summary || "Research response";
    container.append(title);
    [["Claims", answer.claims], ["Unknowns", answer.unknowns]].forEach(([label, values]) => {
      if (!Array.isArray(values) || !values.length) return;
      const heading = document.createElement("strong");
      heading.textContent = label;
      const list = document.createElement("ul");
      values.forEach((value) => {
        const item = document.createElement("li");
        item.textContent = typeof value === "string" ? value : (value.text || JSON.stringify(value));
        list.append(item);
      });
      container.append(heading, list);
    });
    if (Array.isArray(answer.citations) && answer.citations.length) {
      const heading = document.createElement("strong");
      heading.textContent = "Citations";
      container.append(heading);
      answer.citations.forEach((citation) => {
        const detail = document.createElement("details");
        detail.className = "source-detail";
        const summary = document.createElement("summary");
        summary.textContent = `${citation.source_id || "source"} · ${shortId(citation.citation_id)}`;
        const quote = document.createElement("blockquote");
        quote.textContent = citation.excerpt || "No excerpt returned.";
        detail.append(summary, quote);
        if (citation.url && /^https?:\/\//i.test(citation.url)) {
          const link = document.createElement("a");
          link.className = "evidence-link";
          link.href = citation.url;
          link.target = "_blank";
          link.rel = "noreferrer noopener";
          link.textContent = "Open source";
          detail.append(link);
        }
        container.append(detail);
      });
    }
  }

  byId("refresh-dashboard").addEventListener("click", () => loadDashboard(true));
  byId("toggle-live").addEventListener("click", () => {
    state.live = !state.live;
    byId("toggle-live").textContent = state.live ? "Pause live updates" : "Resume live updates";
    scheduleDashboard();
    if (state.live) loadDashboard(false);
  });
  byId("copy-diagnostics").addEventListener("click", copyDiagnostics);
  byId("event-stage").addEventListener("change", renderEvents);
  byId("event-status").addEventListener("change", renderEvents);
  byId("event-search").addEventListener("input", renderEvents);
  byId("refresh-button").addEventListener("click", () => {
    if (state.selectedTopic) selectTopic(state.selectedTopic, true);
  });
  byId("research-form").addEventListener("submit", runResearch);

  Promise.all([loadDashboard(true), loadTopics()]);
  scheduleDashboard();
})();
"""

_LOCAL_AUTH_SCRIPT = """  function bearerToken() {
    return "fi-intel-local";
  }
  function clearBearerToken() {
    return undefined;
  }"""

_FIXTURE_AUTH_SCRIPT = """  function bearerToken() {
    return "stage-one-demo";
  }
  function clearBearerToken() {
    return undefined;
  }"""

STAGE_ONE_JS = _STAGE_ONE_JS_TEMPLATE.replace("__FI_INTEL_TOKEN_PROVIDER__", _LOCAL_AUTH_SCRIPT)
STAGE_ONE_FIXTURE_JS = _STAGE_ONE_JS_TEMPLATE.replace(
    "__FI_INTEL_TOKEN_PROVIDER__", _FIXTURE_AUTH_SCRIPT
)
