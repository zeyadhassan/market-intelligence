# ruff: noqa: E501
"""Self-contained assets for the subscription-first Stage 1 POC page."""

STAGE_ONE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>FI Opportunity Watch</title>
  <link rel="stylesheet" href="/stage-one/assets/stage-one.css">
</head>
<body>
  <a class="skip-link" href="#results">Skip to today's results</a>
  <header class="topbar">
    <a class="brand" href="/stage-one" aria-label="FI Opportunity Watch home">
      <span class="brand-mark" aria-hidden="true">FI</span>
      <span><strong>Opportunity Watch</strong><small>Fresh signals for the topics you follow</small></span>
    </a>
    <div class="header-meta">
      <span class="demo-badge">Local GCC intelligence</span>
      <span class="analyst-name">Demo analyst</span>
    </div>
  </header>

  <main>
    <section class="intro" aria-labelledby="page-title">
      <div>
        <p class="eyebrow">Your daily market-intelligence watchlist</p>
        <h1 id="page-title">Choose what you want to follow.</h1>
        <p>Select a topic to save it and run today's analysis. New opportunities appear below with
           the exact evidence used to produce them.</p>
      </div>
      <div class="freshness-card" aria-label="Live analysis status">
        <span class="pulse" aria-hidden="true"></span>
        <span><strong>Live analysis on demand</strong><small>Official sources + configured models</small></span>
      </div>
    </section>

    <section class="topics-panel" aria-labelledby="topics-heading">
      <div class="section-heading">
        <div><p class="eyebrow">Step 1</p><h2 id="topics-heading">Topics</h2></div>
        <p>Following a topic saves it for the next daily run.</p>
      </div>
      <div id="topic-list" class="topic-list" aria-live="polite">
        <div class="loading-row"><span class="spinner" aria-hidden="true"></span>Loading topics…</div>
      </div>
    </section>

    <section id="results" class="results-panel" aria-labelledby="results-heading">
      <div class="section-heading results-heading">
        <div><p class="eyebrow">Step 2</p><h2 id="results-heading">Today's opportunities</h2></div>
        <button id="refresh-button" class="quiet-button" type="button" disabled>Refresh analysis</button>
      </div>
      <div id="analysis-state" class="analysis-state neutral" role="status" aria-live="polite">
        <strong>Choose a topic above</strong>
        <span>The evidence-backed results for that subscription will appear here.</span>
      </div>
      <div id="coverage-ledger" class="coverage-ledger" hidden></div>
      <div id="result-list" class="result-list"></div>
    </section>

    <aside id="scope-note" class="scope-note">
      <strong>Live-source scope</strong>
      <p>Each run fetches official public GCC sources and calls the configured LLM. The source
         ledger above reports exactly what succeeded; a failed source makes coverage incomplete.</p>
    </aside>
  </main>

  <div id="toast" class="toast" role="status" aria-live="polite"></div>
  <script src="/stage-one/assets/stage-one.js" defer></script>
</body>
</html>
"""

STAGE_ONE_FIXTURE_HTML = (
    STAGE_ONE_HTML.replace("Local GCC intelligence", "Synthetic fixture")
    .replace("Live analysis on demand", "Fixture analysis ready")
    .replace("Official sources + configured models", "No network or LLM calls")
    .replace("Live-source scope", "Fixture scope")
    .replace(
        "Each run fetches official public GCC sources and calls the configured LLM. The source\n"
        "         ledger above reports exactly what succeeded; a failed source makes coverage incomplete.",
        "This explicit fixture mode exercises only the product loop. It does not claim live GCC\n"
        "         coverage or real-world model quality.",
    )
)


STAGE_ONE_CSS = """:root {
  color-scheme: light;
  --ink: #15221d;
  --muted: #627068;
  --line: #dce4df;
  --surface: #ffffff;
  --wash: #f3f6f4;
  --green: #0d6b49;
  --green-dark: #084c35;
  --green-soft: #e6f4ed;
  --blue: #275da8;
  --amber: #8a5a12;
  --amber-soft: #fff3dc;
  --red: #a33a33;
  --red-soft: #f9e9e7;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; background: var(--wash); color: var(--ink); }
button, input, textarea { font: inherit; }
button { cursor: pointer; }
button:focus-visible, input:focus-visible, textarea:focus-visible, summary:focus-visible, a:focus-visible {
  outline: 3px solid rgba(39, 93, 168, .26); outline-offset: 2px;
}
.skip-link { position: fixed; z-index: 20; top: -60px; left: 10px; padding: 9px 12px; background: #fff; color: var(--ink); }
.skip-link:focus { top: 10px; }
.topbar { min-height: 72px; padding: 0 max(22px, calc((100vw - 1120px) / 2)); display: flex; align-items: center;
  justify-content: space-between; gap: 20px; background: #14231d; color: #fff; border-bottom: 3px solid #43a479; }
.brand { display: flex; align-items: center; gap: 11px; color: inherit; text-decoration: none; }
.brand > span:last-child { display: grid; line-height: 1.18; }
.brand small { margin-top: 4px; color: #bdd0c6; font-size: 12px; }
.brand-mark { display: inline-grid; place-items: center; width: 38px; height: 38px; border-radius: 8px;
  background: #dff4e9; color: var(--green-dark); font-weight: 850; letter-spacing: -.04em; }
.header-meta { display: flex; align-items: center; gap: 12px; font-size: 13px; }
.demo-badge { padding: 5px 9px; border: 1px solid #ddb867; border-radius: 999px; background: #41361e; color: #ffe8b1;
  font-size: 11px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase; }
.analyst-name { color: #d5e2dc; }
main { width: min(1120px, calc(100% - 32px)); margin: 0 auto; padding: 42px 0 56px; }
.intro { display: flex; align-items: end; justify-content: space-between; gap: 32px; margin-bottom: 28px; }
.intro > div:first-child { max-width: 720px; }
.eyebrow { margin: 0 0 7px; color: var(--green); font-size: 11px; font-weight: 850; letter-spacing: .1em; text-transform: uppercase; }
h1 { margin: 0; max-width: 680px; font-size: clamp(30px, 4vw, 48px); line-height: 1.05; letter-spacing: -.035em; }
.intro > div:first-child > p:last-child { max-width: 680px; margin: 15px 0 0; color: var(--muted); font-size: 16px; line-height: 1.6; }
.freshness-card { min-width: 280px; padding: 14px 16px; display: flex; align-items: center; gap: 11px; border: 1px solid #b8d8c9;
  border-radius: 12px; background: var(--green-soft); }
.freshness-card > span:last-child { display: grid; }
.freshness-card small { margin-top: 3px; color: #557066; }
.pulse { width: 10px; height: 10px; border-radius: 50%; background: #1b8d60; box-shadow: 0 0 0 5px rgba(27, 141, 96, .14); }
.topics-panel, .results-panel { overflow: hidden; margin-top: 18px; border: 1px solid var(--line); border-radius: 14px; background: var(--surface);
  box-shadow: 0 8px 30px rgba(25, 45, 36, .045); }
.section-heading { min-height: 78px; padding: 16px 20px; display: flex; align-items: center; justify-content: space-between; gap: 20px;
  border-bottom: 1px solid var(--line); }
.section-heading h2 { margin: 0; font-size: 20px; letter-spacing: -.015em; }
.section-heading > p { max-width: 430px; margin: 0; color: var(--muted); font-size: 13px; text-align: right; }
.topic-list { padding: 12px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; }
.topic-option { position: relative; min-width: 0; }
.topic-option input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.topic-option label { min-height: 132px; padding: 15px 15px 46px; display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: 10px;
  background: #fbfcfb; cursor: pointer; transition: border-color .15s, background .15s, transform .15s; }
.topic-option label:hover { border-color: #8bb9a4; transform: translateY(-1px); }
.topic-option input:focus-visible + label { outline: 3px solid rgba(39, 93, 168, .24); outline-offset: 2px; }
.topic-option input:checked + label { border-color: #4b9c77; background: var(--green-soft); box-shadow: inset 0 0 0 1px #4b9c77; }
.topic-option.viewing label { box-shadow: inset 0 0 0 2px var(--green); }
.topic-label-row { display: flex; justify-content: space-between; align-items: start; gap: 8px; }
.topic-label-row strong { line-height: 1.25; }
.follow-state { flex: 0 0 auto; color: var(--muted); font-size: 11px; font-weight: 800; text-transform: uppercase; }
.topic-option input:checked + label .follow-state { color: var(--green); }
.topic-option small { margin-top: 9px; color: var(--muted); line-height: 1.45; }
.topic-view-button { position: absolute; left: 15px; bottom: 11px; min-height: 28px; padding: 4px 8px; border: 0; border-bottom: 1px solid currentColor;
  background: transparent; color: var(--green); font-size: 11px; font-weight: 800; }
.topic-view-button:disabled { border-color: transparent; color: #8b9690; cursor: not-allowed; }
.results-heading { background: #fbfcfb; }
.quiet-button { min-height: 36px; padding: 7px 12px; border: 1px solid #aebbb4; border-radius: 7px; background: #fff; color: var(--ink); }
.quiet-button:disabled { cursor: not-allowed; opacity: .5; }
.analysis-state { margin: 18px 20px 0; padding: 13px 15px; display: flex; align-items: center; gap: 8px; border-radius: 9px; font-size: 13px; }
.analysis-state span { color: inherit; opacity: .82; }
.analysis-state.neutral { background: #eef1ef; color: #4e5d55; }
.analysis-state.loading { background: #edf3fb; color: #315f99; }
.analysis-state.complete { background: var(--green-soft); color: var(--green-dark); }
.analysis-state.incomplete, .analysis-state.failed { background: var(--amber-soft); color: var(--amber); }
.result-list { padding: 4px 20px 20px; }
.result-card { margin-top: 14px; padding: 20px; border: 1px solid var(--line); border-radius: 11px; background: #fff; }
.result-topline { display: flex; justify-content: space-between; align-items: center; gap: 14px; }
.entity-name { color: var(--green); font-size: 12px; font-weight: 800; letter-spacing: .035em; text-transform: uppercase; }
.lifecycle { padding: 4px 8px; border-radius: 999px; background: var(--green-soft); color: var(--green); font-size: 10px; font-weight: 850;
  letter-spacing: .06em; text-transform: uppercase; }
.result-card h3 { margin: 10px 0 8px; font-size: clamp(19px, 2.4vw, 25px); letter-spacing: -.025em; }
.summary { margin: 0; max-width: 850px; color: #405048; font-size: 15px; line-height: 1.62; }
.result-meta { margin: 15px 0 0; display: flex; flex-wrap: wrap; gap: 8px; }
.result-meta span { padding: 5px 8px; border-radius: 6px; background: #f0f3f1; color: #53625a; font-size: 11px; }
.freshness-reason { margin: 13px 0 0; padding-left: 11px; border-left: 3px solid #69ad8d; color: #405d50; font-size: 13px; }
.evidence-details { margin-top: 17px; border-top: 1px solid var(--line); }
.evidence-details summary { padding: 14px 0 8px; color: var(--blue); font-weight: 750; cursor: pointer; }
.evidence-grid { display: grid; gap: 9px; }
.evidence-item { padding: 13px 14px; border: 1px solid #dfe6e2; border-radius: 8px; background: #f9fbfa; }
.evidence-item header { display: flex; justify-content: space-between; gap: 12px; }
.evidence-item strong { font-size: 13px; }
.evidence-item small { color: var(--muted); }
.evidence-item blockquote { margin: 10px 0 0; padding-left: 12px; border-left: 3px solid #7eb89e; color: #36473f; line-height: 1.55; }
.evidence-item a { display: inline-block; margin-top: 9px; color: var(--blue); font-size: 12px; }
.falsifier { margin: 10px 0 0; color: var(--muted); font-size: 12px; }
.evaluation { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line); }
.evaluation-label { margin: 0 0 9px; font-size: 13px; font-weight: 800; }
.evaluation-actions { display: flex; flex-wrap: wrap; gap: 7px; }
.evaluation-actions button { min-height: 34px; padding: 6px 10px; border: 1px solid #afbbb4; border-radius: 7px; background: #fff; color: #33453c; }
.evaluation-actions button:hover, .evaluation-actions button.selected { border-color: #438d6c; background: var(--green-soft); color: var(--green-dark); }
.evaluation-note { width: 100%; min-height: 38px; margin-top: 9px; padding: 8px 10px; resize: vertical; border: 1px solid #b9c3bd; border-radius: 7px; }
.empty-results { padding: 42px 16px 48px; text-align: center; color: var(--muted); }
.empty-results strong { display: block; margin-bottom: 7px; color: var(--ink); font-size: 18px; }
.scope-note { margin-top: 18px; padding: 14px 17px; border: 1px solid #b9d3c6; border-radius: 10px; background: #f1f8f4; color: #365649; font-size: 12px; }
.scope-note p { margin: 5px 0 0; line-height: 1.5; }
.coverage-ledger { margin: 14px 20px 2px; padding: 14px; border: 1px solid var(--line); border-radius: 9px; background: #f8faf9; }
.coverage-summary { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
.coverage-summary strong { font-size: 13px; }
.coverage-summary small { color: var(--muted); }
.source-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
.source-chip { min-width: 0; padding: 8px 9px; border: 1px solid #dce4df; border-radius: 7px; background: #fff; color: inherit; text-decoration: none; }
.source-chip strong, .source-chip small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-chip strong { font-size: 11px; }
.source-chip small { margin-top: 3px; color: var(--muted); font-size: 10px; }
.source-chip.complete { border-color: #9ac9b2; }
.source-chip.fetch_failed, .source-chip.analysis_failed { border-color: #dfb0aa; background: var(--red-soft); }
.loading-row { grid-column: 1 / -1; min-height: 90px; display: flex; align-items: center; justify-content: center; gap: 9px; color: var(--muted); }
.spinner { width: 17px; height: 17px; border: 2px solid #b9ccc2; border-top-color: var(--green); border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.toast { position: fixed; z-index: 30; right: 18px; bottom: 18px; max-width: min(430px, calc(100vw - 36px)); padding: 11px 15px;
  border-radius: 8px; background: #14231d; color: #fff; box-shadow: 0 8px 30px rgba(0,0,0,.25); opacity: 0; transform: translateY(8px);
  pointer-events: none; transition: opacity .16s, transform .16s; }
.toast.visible { opacity: 1; transform: translateY(0); }
@media (max-width: 900px) {
  .topic-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .intro { align-items: stretch; flex-direction: column; }
  .freshness-card { min-width: 0; }
  .source-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 580px) {
  .topbar { padding: 10px 14px; }
  .brand small, .analyst-name { display: none; }
  main { width: min(100% - 20px, 1120px); padding-top: 28px; }
  .topic-list { grid-template-columns: 1fr; }
  .section-heading { align-items: flex-start; flex-direction: column; gap: 8px; }
  .section-heading > p { text-align: left; }
  .results-heading { flex-direction: row; align-items: center; }
  .analysis-state { align-items: flex-start; flex-direction: column; }
  .result-card { padding: 16px; }
  .source-grid { grid-template-columns: 1fr; }
}
"""


_STAGE_ONE_JS_TEMPLATE = """(() => {
  "use strict";

__FI_INTEL_TOKEN_PROVIDER__
  const state = { topics: [], selectedTopic: null, resultSets: new Map() };
  const byId = (id) => document.getElementById(id);
  const toast = byId("toast");

  function notify(message) {
    toast.textContent = message;
    toast.classList.add("visible");
    window.setTimeout(() => toast.classList.remove("visible"), 2800);
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${bearerToken()}`);
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      if (response.status === 401) clearBearerToken();
      let detail = `Request failed (${response.status})`;
      try { detail = (await response.json()).detail || detail; } catch (_) { /* non-JSON error */ }
      throw new Error(detail);
    }
    return response.json();
  }

  const formatDate = (value) => value ? new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short"
  }).format(new Date(value)) : "-";

  function renderTopics() {
    const list = byId("topic-list");
    list.replaceChildren();
    state.topics.forEach((topic) => {
      const wrapper = document.createElement("div");
      wrapper.className = `topic-option${state.selectedTopic === topic.topic_id ? " viewing" : ""}`;
      const input = document.createElement("input");
      input.type = "checkbox";
      input.id = `topic-${topic.topic_id}`;
      input.checked = topic.subscribed;
      const label = document.createElement("label");
      label.htmlFor = input.id;
      const row = document.createElement("span");
      row.className = "topic-label-row";
      const name = document.createElement("strong");
      name.textContent = topic.label;
      const followState = document.createElement("span");
      followState.className = "follow-state";
      followState.textContent = topic.subscribed ? "Following" : "Follow";
      row.append(name, followState);
      const description = document.createElement("small");
      description.textContent = topic.description;
      label.append(row, description);
      const viewButton = document.createElement("button");
      viewButton.type = "button";
      viewButton.className = "topic-view-button";
      viewButton.textContent = state.selectedTopic === topic.topic_id ? "Viewing results" : "View results";
      viewButton.disabled = !topic.subscribed;
      viewButton.addEventListener("click", () => selectTopic(topic.topic_id));
      input.addEventListener("change", () => toggleTopic(topic.topic_id, input.checked));
      wrapper.append(input, label, viewButton);
      list.append(wrapper);
    });
  }

  function setAnalysisState(kind, title, detail) {
    const panel = byId("analysis-state");
    panel.className = `analysis-state ${kind}`;
    panel.replaceChildren();
    const strong = document.createElement("strong");
    strong.textContent = title;
    const span = document.createElement("span");
    span.textContent = detail;
    panel.append(strong, span);
  }

  async function toggleTopic(topicId, active) {
    try {
      await api(`/v1/topics/${encodeURIComponent(topicId)}/subscription`, {
        method: "PUT", body: JSON.stringify({ active })
      });
      const topic = state.topics.find((item) => item.topic_id === topicId);
      if (topic) topic.subscribed = active;
      renderTopics();
      notify(active ? "Topic added to your daily watchlist." : "Topic removed from your watchlist.");
      if (active) await selectTopic(topicId);
      else if (state.selectedTopic === topicId) {
        const next = state.topics.find((item) => item.subscribed);
        if (next) await selectTopic(next.topic_id);
        else {
          state.selectedTopic = null;
          byId("refresh-button").disabled = true;
          byId("results-heading").textContent = "Today's opportunities";
          byId("result-list").replaceChildren();
          setAnalysisState("neutral", "Choose a topic above", "The evidence-backed results for that subscription will appear here.");
        }
      }
    } catch (error) {
      const topic = state.topics.find((item) => item.topic_id === topicId);
      if (topic) topic.subscribed = !active;
      renderTopics();
      notify(error.message);
    }
  }

  async function selectTopic(topicId, force = false) {
    state.selectedTopic = topicId;
    renderTopics();
    byId("refresh-button").disabled = false;
    const topic = state.topics.find((item) => item.topic_id === topicId);
    setAnalysisState("loading", "Running analysis…", `${topic?.label || "Selected topic"} · loading evidence-backed results`);
    byId("coverage-ledger").hidden = true;
    byId("result-list").replaceChildren();
    try {
      let resultSet = state.resultSets.get(topicId);
      if (!resultSet || force) {
        const refreshQuery = force ? "?refresh=true" : "";
        resultSet = await api(`/v1/topics/${encodeURIComponent(topicId)}/results${refreshQuery}`);
        state.resultSets.set(topicId, resultSet);
      }
      if (state.selectedTopic !== topicId) return;
      renderResultSet(resultSet);
      if (["queued", "running"].includes(resultSet.analysis_status)) {
        window.setTimeout(() => {
          state.resultSets.delete(topicId);
          if (state.selectedTopic === topicId) selectTopic(topicId);
        }, 2000);
      }
    } catch (error) {
      setAnalysisState("failed", "Analysis could not be shown", error.message);
      notify(error.message);
    }
  }

  function evidenceNode(item) {
    const article = document.createElement("article");
    article.className = "evidence-item";
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = item.title;
    const source = document.createElement("small");
    source.textContent = `${item.country || "GCC"} · ${item.source_id} · published ${formatDate(item.published_at)}`;
    header.append(title, source);
    const quote = document.createElement("blockquote");
    quote.textContent = item.quote;
    article.append(header, quote);
    if (item.content_hash) {
      const provenance = document.createElement("small");
      provenance.textContent = `Fetched ${formatDate(item.fetched_at)} · SHA-256 ${item.content_hash.slice(0, 12)}…`;
      article.append(provenance);
    }
    if (item.source_url) {
      const link = document.createElement("a");
      link.href = item.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Open original source";
      article.append(link);
    }
    return article;
  }

  function evaluationLabel(verdict) {
    return {
      useful: "Useful",
      not_relevant: "Not relevant",
      incorrect: "Incorrect",
      duplicate: "Duplicate",
      too_old: "Too old"
    }[verdict] || verdict;
  }

  function resultNode(result) {
    const article = document.createElement("article");
    article.className = "result-card";
    article.dataset.resultId = result.result_id;

    const topline = document.createElement("div");
    topline.className = "result-topline";
    const entity = document.createElement("span");
    entity.className = "entity-name";
    entity.textContent = result.entity_name;
    const lifecycle = document.createElement("span");
    lifecycle.className = "lifecycle";
    lifecycle.textContent = result.lifecycle_state;
    topline.append(entity, lifecycle);

    const title = document.createElement("h3");
    title.textContent = result.title;
    const summary = document.createElement("p");
    summary.className = "summary";
    summary.textContent = result.summary;

    const analystContext = document.createElement("div");
    analystContext.className = "analyst-context";
    [
      ["Why now", result.why_now],
      ["Commercial angle", result.commercial_angle],
      ["Materiality", result.materiality],
      ["What changed", result.change_summary],
      ["Coverage and freshness", result.coverage_details],
      ["Uncertainty", result.uncertainty]
    ].forEach(([label, value]) => {
      if (!value) return;
      const line = document.createElement("p");
      const strong = document.createElement("strong");
      strong.textContent = `${label}: `;
      line.append(strong, document.createTextNode(value));
      analystContext.append(line);
    });
    if (result.contradictions?.length) {
      const line = document.createElement("p");
      const strong = document.createElement("strong");
      strong.textContent = "Contradictions: ";
      line.append(strong, document.createTextNode(result.contradictions.join("; ")));
      analystContext.append(line);
    }

    const meta = document.createElement("div");
    meta.className = "result-meta";
    [
      `Uncalibrated relevance ${Math.round(result.score * 100)}/100`,
      `As of ${formatDate(result.as_of)}`,
      `${result.evidence.length} evidence source${result.evidence.length === 1 ? "" : "s"}`
    ].forEach((text) => {
      const item = document.createElement("span");
      item.textContent = text;
      meta.append(item);
    });
    const freshness = document.createElement("p");
    freshness.className = "freshness-reason";
    freshness.textContent = result.freshness_reason;

    const details = document.createElement("details");
    details.className = "evidence-details";
    const detailsSummary = document.createElement("summary");
    detailsSummary.textContent = "View evidence and what would disprove this";
    const evidenceGrid = document.createElement("div");
    evidenceGrid.className = "evidence-grid";
    result.evidence.forEach((item) => evidenceGrid.append(evidenceNode(item)));
    const falsifier = document.createElement("p");
    falsifier.className = "falsifier";
    falsifier.textContent = `Would be disproved if: ${result.falsifier}`;
    details.append(detailsSummary, evidenceGrid, falsifier);

    const evaluation = document.createElement("section");
    evaluation.className = "evaluation";
    const evaluationTitle = document.createElement("p");
    evaluationTitle.className = "evaluation-label";
    evaluationTitle.textContent = "Was this opportunity useful?";
    const actions = document.createElement("div");
    actions.className = "evaluation-actions";
    ["useful", "not_relevant", "incorrect", "duplicate", "too_old"].forEach((verdict) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = evaluationLabel(verdict);
      button.dataset.verdict = verdict;
      if (result.latest_evaluation === verdict) button.classList.add("selected");
      button.addEventListener("click", () => evaluateResult(result, verdict, article));
      actions.append(button);
    });
    const note = document.createElement("textarea");
    note.className = "evaluation-note";
    note.maxLength = 1000;
    note.rows = 1;
    note.placeholder = "Optional note — what made this useful or wrong?";
    note.setAttribute("aria-label", `Optional evaluation note for ${result.title}`);
    evaluation.append(evaluationTitle, actions, note);

    if (result.investigation_trace?.length) {
      const trace = document.createElement("details");
      trace.className = "evidence-details";
      const traceSummary = document.createElement("summary");
      traceSummary.textContent = "View bounded investigation trace";
      const list = document.createElement("ol");
      result.investigation_trace.forEach((step) => {
        const item = document.createElement("li");
        item.textContent = `${step.operation || "step"}: ${step.reason || step.status || "complete"}`;
        list.append(item);
      });
      trace.append(traceSummary, list);
      details.append(trace);
    }
    article.append(topline, title, summary, analystContext, meta, freshness, details, evaluation);
    return article;
  }

  async function evaluateResult(result, verdict, card) {
    const note = card.querySelector(".evaluation-note").value.trim();
    try {
      await api(`/v1/results/${encodeURIComponent(result.result_id)}/evaluation`, {
        method: "POST", body: JSON.stringify({ verdict, note })
      });
      result.latest_evaluation = verdict;
      card.querySelectorAll(".evaluation-actions button").forEach((button) => {
        button.classList.toggle("selected", button.dataset.verdict === verdict);
      });
      notify(`${evaluationLabel(verdict)} evaluation recorded.`);
    } catch (error) { notify(error.message); }
  }

  function renderResultSet(resultSet) {
    const complete = resultSet.coverage_state === "complete";
    setAnalysisState(
      complete ? "complete" : "incomplete",
      resultSet.message,
      `Analysis as of ${formatDate(resultSet.as_of)} · ${resultSet.coverage_state.replaceAll("_", " ")} coverage`
    );
    if (resultSet.mode === "fixture") byId("coverage-ledger").hidden = true;
    else renderCoverageLedger(resultSet);
    const scopeParagraph = byId("scope-note").querySelector("p");
    if (resultSet.scope_notice) scopeParagraph.textContent = resultSet.scope_notice;
    byId("results-heading").textContent = resultSet.label;
    const list = byId("result-list");
    list.replaceChildren();
    if (!resultSet.results.length) {
      const empty = document.createElement("div");
      empty.className = "empty-results";
      const strong = document.createElement("strong");
      strong.textContent = complete ? "Nothing new for this topic" : "No result can be claimed";
      const detail = document.createElement("span");
      detail.textContent = complete
        ? (resultSet.mode === "fixture"
          ? "The deterministic fixture produced no result above its triage threshold."
          : "All required authorized sources completed and produced no supported result for this topic.")
        : "Required coverage did not complete, so silence is not treated as a result.";
      empty.append(strong, detail);
      list.append(empty);
      return;
    }
    resultSet.results.forEach((result) => list.append(resultNode(result)));
  }

  function renderCoverageLedger(resultSet) {
    const ledger = byId("coverage-ledger");
    ledger.replaceChildren();
    ledger.hidden = false;
    const summary = document.createElement("div");
    summary.className = "coverage-summary";
    const label = document.createElement("strong");
    label.textContent = `Live source ledger: ${resultSet.successful_source_count}/${resultSet.required_source_count} completed`;
    const model = document.createElement("small");
    model.textContent = `Model ${resultSet.model_name || "unavailable"} · run ${resultSet.run_id || "unknown"} · ${resultSet.rejected_candidate_count} unsupported candidate(s) rejected`;
    summary.append(label, model);
    const grid = document.createElement("div");
    grid.className = "source-grid";
    (resultSet.source_statuses || []).forEach((item) => {
      const chip = document.createElement("a");
      chip.className = `source-chip ${item.status}`;
      chip.href = item.source_url;
      chip.target = "_blank";
      chip.rel = "noopener noreferrer";
      chip.title = `${item.detail} ${item.content_hash ? `SHA-256 ${item.content_hash}` : ""}`;
      const name = document.createElement("strong");
      name.textContent = `${item.country} · ${item.display_name}`;
      const status = document.createElement("small");
      status.textContent = `${item.status.replaceAll("_", " ")} · ${item.candidate_count} accepted`;
      chip.append(name, status);
      grid.append(chip);
    });
    ledger.append(summary, grid);
  }

  async function loadTopics() {
    try {
      state.topics = await api("/v1/topics");
      renderTopics();
      const firstSubscribed = state.topics.find((topic) => topic.subscribed);
      if (firstSubscribed) await selectTopic(firstSubscribed.topic_id);
    } catch (error) {
      byId("topic-list").textContent = error.message;
      notify(error.message);
    }
  }

  byId("refresh-button").addEventListener("click", () => {
    if (state.selectedTopic) selectTopic(state.selectedTopic, true);
  });
  loadTopics();
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
