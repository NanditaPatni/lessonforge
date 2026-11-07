/* LessonForge single-page UI (vanilla JS, no build step). */
"use strict";

const SAMPLES = {
  http: { title: "HTTP Requests and Responses",
          objective: "Explain GET and POST methods and distinguish between 2xx, 4xx, and 5xx response status categories." },
  sql:  { title: "SQL Joins: INNER JOIN vs LEFT JOIN",
          objective: "Differentiate INNER JOIN and LEFT JOIN and predict the rows each returns for a given pair of tables, including how NULLs appear for unmatched rows." },
};
const STEPS = ["plan", "research", "generate", "qc_research", "qc_instruction"];
const STEP_HELP = {
  plan: "Breaks the objective into measurable sub-objectives and plans the searches.",
  research: "Finds web pages, books and YouTube videos and rates how trustworthy each is.",
  generate: "Writes the 45-minute lesson using only the research evidence, with citations.",
  qc_research: "Checks every factual claim against the sources and an independent reviewer.",
  qc_instruction: "Checks the lesson teaches, practises and tests every objective, and fits 45 minutes.",
};
const TYPES = ["hook", "concept", "example", "activity", "summary"];

const S = { health: null, lessons: [], id: null, data: null, tab: "preview", draft: null, base: null, dirty: false, poll: null, qcSel: {} };

/* ------------------------------------------------------------- utilities */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));

// The "Server unreachable" chip tracks every request, not just the first health check.
let serverDown = false;
function setServerDown(down) {
  if (down === serverDown) return;
  serverDown = down;
  if (down) $("#health").innerHTML = `<span class="chip bad" title="Is the run.bat window still open?">Server unreachable</span>`;
  else loadHealth();
}

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
                              body: opts.body ? JSON.stringify(opts.body) : undefined });
  } catch (e) { setServerDown(true); throw new Error("Server unreachable — is the run.bat window still open?"); }
  setServerDown(false);
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    let msg = data && data.detail ? data.detail : `HTTP ${res.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg.replace("Value error, ", "")).join("; ");
    const err = new Error(msg); err.status = res.status; err.data = data; throw err;
  }
  return data;
}

function toast(msg, error = false) {
  const t = $("#toast");
  t.innerHTML = `<div class="toast-title">${error ? "Error" : "Notice"}<i class="wb-close"></i></div>
    <div class="toast-body"><span class="toast-icon">${error ? "!" : "i"}</span><span></span></div>`;
  t.querySelector(".toast-body span:last-child").textContent = msg;
  t.className = "toast" + (error ? " error" : "");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), error ? 7000 : 3500);
}

function getPath(obj, path) { return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj); }
function setPath(obj, path, val) {
  const ks = path.split("."); const last = ks.pop();
  const target = ks.reduce((o, k) => o[k], obj); target[last] = val;
}

/* ------------------------------------------------- timing (mirrors server) */
function stripMd(md) {
  return (md || "").replace(/```[\s\S]*?```/g, " ").replace(/`[^`]*`/g, " ").replace(/\[S\d+(?:\.p\d+)?(?:\s*[,;]\s*S\d+(?:\.p\d+)?)*\]/g, " ")
    .replace(/[#>*_|\-]+/g, " ").replace(/\s+/g, " ").trim();
}
const words = (md) => (stripMd(md) ? stripMd(md).split(" ").length : 0);
const codeLines = (md) => [...(md || "").matchAll(/```.*?\n([\s\S]*?)```/g)].reduce((n, m) => n + m[1].trim().split("\n").length, 0);
// Source ids cited in markdown: [S1], [S1, S3] and passage forms like [S6.p8] all count (S6.p8 -> S6).
function citesOf(md) {
  const ids = new Set();
  for (const m of (md || "").matchAll(/\[(S\d+(?:\.p\d+)?(?:\s*[,;]\s*S\d+(?:\.p\d+)?)*)\]/g))
    for (const t of m[1].split(/[,;]/)) ids.add(t.trim().split(".")[0]);
  return [...ids].sort((a, b) => +a.slice(1) - +b.slice(1)).join(" ") || "—";
}
function estimate(lesson) {
  const r = S.health?.reading_wpm || 180, sp = S.health?.speaking_wpm || 130;
  let self = 0, inst = 0;
  for (const s of lesson.sections) {
    const w = words(s.content_md), c = codeLines(s.content_md), a = s.type === "activity" ? +s.activity_minutes || 0 : 0;
    self += w / r + c * 0.1 + a; inst += (w / sp + c * 0.15) * 1.15 + a;
  }
  self += lesson.quiz.length; inst += lesson.quiz.length * 1.5;
  const srcs = Object.fromEntries((S.data?.research?.sources || []).map((s) => [s.id, s]));
  for (const v of lesson.videos || []) if (v.placement === "in_lesson") { const m = (srcs[v.source_id]?.duration_sec || 0) / 60; self += m; inst += m; }
  return { self: Math.round(self * 10) / 10, inst: Math.round(inst * 10) / 10 };
}
function timingChip(self, inst) {
  const b = S.health?.lesson_minutes || 45, worst = Math.max(self, inst);
  const cls = worst > b + 1 ? "bad" : self < b * 0.5 ? "warn" : "ok";
  const why = cls === "ok" ? "fits the budget" : cls === "bad" ? "too long" : "too short";
  const mark = cls === "ok" ? "✓" : cls === "bad" ? "too long" : "too short";
  return `<span class="chip ${cls}" title="Estimated time: ${self} min self-paced / ${inst} min instructor-led — ${why}. Must be at most ${b} min.">⏱ ${self}–${inst} min ${mark}</span>`;
}

/* ---------------------------------------------------------------- health */
async function loadHealth() {
  try {
    S.health = await api("/api/health");
    const h = S.health;
    // Only show something when the user needs to act; a healthy setup shows nothing.
    $("#health").innerHTML = [
      h.llm_key_set ? "" : `<span class="chip bad" title="Run setup.bat or doctor.bat to add a key">⚠ API key missing</span>`,
      h.research_mode === "mock" ? `<span class="chip warn" title="RESEARCH_MODE=mock in .env">⚠ Offline demo data</span>` : "",
    ].join("");
  } catch (e) { serverDown = true; $("#health").innerHTML = `<span class="chip bad" title="Is the run.bat window still open?">Server unreachable</span>`; }
}

/* ------------------------------------------------------------ lesson list */
async function loadLessons() {
  try { S.lessons = await api("/api/lessons"); } catch { return; }
  const el = $("#lessonList");
  if (!S.lessons.length) { el.innerHTML = `<div class="muted small">No lessons yet.</div>`; return; }
  el.innerHTML = S.lessons.map((l) => `
    <div class="lesson-item ${l.id === S.id ? "active" : ""}" data-open="${l.id}">
      <div class="t">${esc(l.title)}</div>
      <div class="muted small">${esc(l.updated_at)}${l.latest_version ? ` · v${l.latest_version}` : ""}</div>
      <div class="dots">${STEPS.map((s) => `<span class="dot ${l.steps[s]}" title="${s}: ${l.steps[s]}"></span>`).join("")}</div>
    </div>`).join("");
}

/* ----------------------------------------------------------- create form */
$("#createForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = $("#fTitle").value.trim(), objective = $("#fObjective").value.trim();
  const err = $("#formError");
  if (title.length < 3) { err.textContent = "Please enter a lesson title (at least 3 characters)."; $("#fTitle").focus(); return; }
  if (objective.length < 10) { err.textContent = "Please enter a learning objective (at least 10 characters)."; $("#fObjective").focus(); return; }
  err.textContent = "";
  $("#createBtn").disabled = true;
  try {
    const { id } = await api("/api/lessons", { method: "POST", body: { title, objective } });
    $("#fTitle").value = ""; $("#fObjective").value = "";
    await openLesson(id);
  } catch (ex) { err.textContent = ex.message; }
  finally { $("#createBtn").disabled = false; }
});
document.querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", () => {
  const s = SAMPLES[b.dataset.sample]; $("#fTitle").value = s.title; $("#fObjective").value = s.objective;
}));
$("#lessonList").addEventListener("click", (e) => {
  const item = e.target.closest("[data-open]");
  if (item) openLesson(item.dataset.open);
});

/* ------------------------------------------------------------ open lesson */
async function openLesson(id) {
  if (S.dirty && S.id !== id && !confirm("You have unsaved edits. They are kept as a browser draft. Switch lesson?")) return;
  S.logOpen = false; S.id = id; S.draft = null; S.base = null; S.dirty = false; S.qcSel = {};
  await refresh(true);
  loadLessons();
}

async function refresh(force = false) {
  if (!S.id) return;
  let d;
  try { d = await api(`/api/lessons/${S.id}`); }
  catch (e) { toast(e.message, true); return; }
  const prevVersion = S.data?.version?.version;
  S.data = d;
  const newVersion = d.version?.version;
  if (d.version && (force || (!S.dirty && newVersion !== prevVersion) || !S.draft)) {
    S.draft = clone(d.version.content); S.base = newVersion; S.dirty = false;
  }
  render(force || newVersion !== prevVersion || (!S.dirty && S.tab === "editor" && !$("#tabBody")?.innerHTML));
  clearTimeout(S.poll);
  if (d.running) S.poll = setTimeout(() => { refresh(); loadLessons(); }, 1500);
}

/* ------------------------------------------------------------------ render */
function render(full = true) {
  const d = S.data, main = $("#main");
  if (!d) return;
  if (!$("#lessonHead") || main.dataset.lesson !== S.id) {
    main.dataset.lesson = S.id;
    main.innerHTML = `<section class="win"><div class="win-title t-purple"><span id="winTitle">Lesson</span><span class="win-btns" aria-hidden="true"><i class="wb-min"></i><i class="wb-max"></i><i class="wb-close"></i></span></div>
      <div class="win-body"><div id="lessonHead"></div><div id="tabBody"></div></div></section>`;
    full = true;
  }
  const q1 = d.qc_research, q2 = d.qc_instruction;
  const openIssues = (r) => (r ? r.issues.filter((i) => i.status === "open").length : 0);
  const counts = { qc1: openIssues(q1), qc2: openIssues(q2) };
  const y = window.scrollY;
  const wt = $("#winTitle"); if (wt) wt.textContent = `Lesson · ${d.lesson.title}`;
  const logOpen = S.logOpen || STEPS.some((n) => d.steps[n].status === "failed");
  $("#lessonHead").innerHTML = `
      <div class="head">
        <div><h2>${esc(d.lesson.title)}</h2><div class="muted">${esc(d.lesson.objective)}</div></div>
        <div class="badges">
          ${d.version ? `<span class="chip dark">v${d.version.version}</span>` : ""}
          ${d.timing ? timingChip(d.timing.self_paced, d.timing.instructor_led) : ""}
          ${q1 ? `<span class="chip ${q1.status}" title="Fact check: share of claims backed by the research sources. 'fail' means at least one critical issue to look at.">QC1 ${q1.status.replace("_", " ")} · ${q1.metrics.grounding_score}% grounded</span>` : ""}
          ${q2 ? `<span class="chip ${q2.status}" title="Teaching-quality score from the rubric and automated checks. 'fail' means at least one critical issue to look at.">QC2 ${q2.status.replace("_", " ")}${q2.metrics.instructional_score != null ? ` · ${q2.metrics.instructional_score}/100` : ""}</span>` : ""}
          <button class="btn small danger" data-act="delete" title="Delete this lesson and all its versions">Delete</button>
        </div>
      </div>
      ${renderGuide(d)}
      ${renderPipeline(d)}
      <details class="log" ${d.running || logOpen ? "open" : ""}><summary>Activity log (${d.events.length})</summary>
        <div class="log-body" id="logBody">${d.events.map((e) => `<div class="${e.level}">${esc(e.ts.slice(11))}  ${esc(e.message)}</div>`).join("")}</div>
      </details>
      ${d.version ? renderFlowNav(counts)
        : (d.running ? `<p class="muted"><span class="spinner"></span> Working… the lesson appears here when generation finishes.</p>` : "")}`;
  const lb = $("#logBody"); if (lb) lb.scrollTop = lb.scrollHeight;
  // Never rebuild the editor while the instructor is typing (keeps focus + cursor); other tabs refresh freely.
  if (full || S.tab !== "editor") {
    $("#tabBody").innerHTML = d.version ? renderTab() : "";
    if (d.version) afterTab();
  }
  window.scrollTo(0, y);
}

// What should the user do now? One clear instruction + one button, based on the lesson's state.
function nextStep(d) {
  const latest = d.version?.version;
  const failed = STEPS.find((n) => d.steps[n].status === "failed");
  const runningStep = STEPS.findIndex((n) => d.steps[n].status === "running");
  const open = (r) => (r ? r.issues.filter((i) => i.status === "open") : []);
  const q1 = open(d.qc_research), q2 = open(d.qc_instruction);
  const crit = [...q1, ...q2].filter((i) => i.severity === "critical").length;
  const stale = latest && ((d.qc_research && d.qc_research.version !== latest) || (d.qc_instruction && d.qc_instruction.version !== latest));
  if (d.running) return { icon: "⏳", text: runningStep >= 0
    ? `Working: <b>${esc(d.steps[STEPS[runningStep]].label)}</b> (${runningStep + 1}/5)…`
    : "Working…" };
  if (failed) return { icon: "!", text: `<b>${esc(d.steps[failed].label)}</b> failed.`,
    btn: ["Retry now", "retry", failed] };
  if (!latest) return { icon: "▶", text: "Lesson not generated yet.", btn: ["Generate", "retry", null] };
  if (stale) return { icon: "↻", text: `Lesson changed — check v${latest} again.`, btn: ["Check again", "rerun-qc"] };
  if (crit) return { icon: "!", text: `Check <b>${crit} critical issue${crit > 1 ? "s" : ""}</b> first.`,
    btn: ["Go to " + (q1.some((i) => i.severity === "critical") ? "Fact check" : "Teaching check"), "tab", q1.some((i) => i.severity === "critical") ? "qc1" : "qc2"] };
  if (q1.length) return { icon: "1", text: `Review <b>${q1.length} fact issue${q1.length > 1 ? "s" : ""}</b>: apply or dismiss.`, btn: ["Go to Fact check", "tab", "qc1"] };
  if (q2.length) return { icon: "2", text: `Review <b>${q2.length} teaching issue${q2.length > 1 ? "s" : ""}</b>: apply or dismiss.`, btn: ["Go to Teaching check", "tab", "qc2"] };
  return { icon: "✓", text: "All issues handled — ready to export.", btn: ["Go to Export", "tab", "export"] };
}

// The workflow as a numbered path. "Lesson" covers both Preview and Edit.
const FLOW = [["sources", "Sources"], ["lesson", "Lesson"], ["qc1", "Fact check"], ["qc2", "Teaching check"], ["export", "Export"]];
const navKey = () => (S.tab === "editor" || S.tab === "preview" ? "lesson" : S.tab);
const tabFor = (k) => (k === "lesson" ? "preview" : k);

function renderFlowNav(counts) {
  const cur = navKey();
  return `<nav class="flow">${FLOW.map(([k, label], i) => `
      <button class="flow-step ${cur === k ? "active" : ""}" data-tab="${tabFor(k)}" title="${esc((TAB_INTRO[k === "lesson" ? "preview" : k] || "").replace(/<[^>]+>/g, ""))}">
        <span class="num">${i + 1}</span>${label}${counts[k] ? `<span class="count" title="open issues">${counts[k]}</span>` : ""}</button>`).join('<span class="flow-arrow" aria-hidden="true">›</span>')}
      <button class="flow-hist ${cur === "versions" ? "active" : ""}" data-tab="versions" title="Every saved version — compare or restore">History</button>
    </nav>`;
}

function renderFlowFooter() {
  const keys = FLOW.map(([k]) => k);
  const i = keys.indexOf(navKey());
  if (i < 0) return `<div class="flow-footer"><button class="btn" data-tab="preview">← Back to the lesson</button></div>`;
  const prev = i > 0 ? FLOW[i - 1] : null, next = i < FLOW.length - 1 ? FLOW[i + 1] : null;
  return `<div class="flow-footer">
    ${prev ? `<button class="btn" data-tab="${tabFor(prev[0])}">← ${prev[1]}</button>` : "<span></span>"}
    ${next ? `<button class="btn primary" data-tab="${tabFor(next[0])}">Next: ${next[1]} →</button>` : ""}
  </div>`;
}

function renderPipeline(d) {
  const failed = STEPS.some((n) => d.steps[n].status === "failed");
  if (d.running || failed || !d.version) return renderStepper();
  return `<details class="pipeline"><summary>✓ Researched, written and checked <span class="muted small">(show the 5 steps)</span></summary>${renderStepper()}</details>`;
}

function renderGuide(d) {
  const g = nextStep(d);
  const btn = g.btn && !(g.btn[1] === "tab" && g.btn[2] === S.tab) ? (g.btn[1] === "tab"
    ? `<button class="btn primary small" data-tab="${g.btn[2]}">${g.btn[0]}</button>`
    : `<button class="btn primary small" data-act="${g.btn[1]}" ${g.btn[2] ? `data-step="${g.btn[2]}"` : ""}>${g.btn[0]}</button>`) : "";
  return `<div class="guide"><span class="guide-icon">${g.icon}</span><span class="guide-text">${g.text}</span>${btn}</div>`;
}

function renderStepper() {
  const d = S.data;
  return `<div class="stepper">${STEPS.map((name, i) => {
    const st = d.steps[name];
    const icon = { done: "✓", running: '<span class="spinner"></span>', failed: "✗", pending: "○" }[st.status];
    const canRetry = !d.running && st.status === "failed";
    const canRerun = !d.running && st.status === "done";
    return `<div class="step ${st.status}"><div class="seg-bar ${st.status}" aria-hidden="true"><span></span></div>
      <div class="n">Step ${i + 1}</div>
      <div class="l" title="${esc(STEP_HELP[name] || "")}">${icon} ${esc(st.label)}</div>
      ${st.error ? `<div class="err">${esc(st.error)}</div>` : ""}
      <div class="actions">
        ${canRetry ? `<button class="btn small primary" data-act="retry" data-step="${name}">Retry</button>` : ""}
        ${canRerun ? `<button class="btn small ghost" data-act="rerun" data-step="${name}" title="Run this step again (and the steps after it). Earlier steps and all saved versions are kept.">↻ redo</button>` : ""}
      </div></div>`;
  }).join("")}</div>`;
}

function lessonSwitch() {
  return `<div class="lesson-bar"><div class="seg-switch" role="tablist">
      <button class="${S.tab === "preview" ? "on" : ""}" data-tab="preview">Preview</button>
      <button class="${S.tab === "editor" ? "on" : ""}" data-tab="editor">✎ Edit</button></div>
    ${S.tab === "preview" ? `<a class="small" target="_blank" href="/api/lessons/${S.id}/preview">open full page ↗</a>` : ""}</div>`;
}

function renderTab() {
  switch (S.tab) {
    case "editor": return lessonSwitch() + renderEditor() + renderFlowFooter();
    case "preview": return lessonSwitch() + (S.dirty ? '<p class="dirty">You have unsaved edits — save them in Edit to see them here.</p>' : "")
      + `<iframe class="preview" src="/api/lessons/${S.id}/preview?t=${Date.now()}"></iframe>` + renderFlowFooter();
    case "sources": return renderSources() + renderFlowFooter();
    case "qc1": return renderQC1() + renderFlowFooter();
    case "qc2": return renderQC2() + renderFlowFooter();
    case "versions": return `<div id="versions" class="muted">Loading…</div>` + renderFlowFooter();
    case "export": return renderExport() + renderFlowFooter();
  }
  return "";
}

const TAB_INTRO = {
  editor: "Change anything below. <b>Ctrl+S</b> (or the button) saves a new version — older versions are always kept.",
  preview: "This is exactly what learners will see.",
  sources: "Everything the lesson is based on. Each source has a trust level and the reason for it.",
  qc1: "<b>Fact check.</b> Every claim is checked against the sources and by an independent reviewer. Apply or dismiss each suggestion.",
  qc2: "<b>Teaching check.</b> Is every objective taught, practised and tested? Is it clear, and does it fit 45 minutes?",
  versions: "Every save, AI generation and applied fix is a version. Compare any two, or restore an older one.",
  export: "Download the finished lesson, its references and both QC reports.",
};

function afterTab() {
  if (S.tab === "versions") loadVersions();
  if (S.tab === "editor") document.querySelectorAll("textarea.content").forEach(autosize);
}

/* ------------------------------------------------------------------ editor */
function renderEditor() {
  const L = S.draft; if (!L) return "";
  const est = estimate(L);
  const los = L.sub_objectives || [];
  const draftKey = `lf-draft-${S.id}`;
  let draftBanner = "";
  try {
    const saved = JSON.parse(localStorage.getItem(draftKey) || "null");
    if (saved && !S.dirty && JSON.stringify(saved.content) !== JSON.stringify(S.data.version.content)) {
      draftBanner = `<div class="banner warn">An unsaved browser draft (based on v${saved.base}, ${esc(saved.at)}) was found.
        <button class="btn small" data-act="restore-draft">Restore draft</button><button class="btn small" data-act="discard-draft">Discard</button></div>`;
    }
  } catch { /* storage unavailable */ }
  const stale = S.base !== S.data.version.version
    ? `<div class="banner bad">You are editing v${S.base} but v${S.data.version.version} is now the latest. Saving will be rejected; your draft is kept in the browser.</div>` : "";
  return `${draftBanner}${stale}
  <div class="toolbar">
    <button class="btn primary" data-act="save" ${S.dirty ? "" : "disabled"}>Save as new version</button>
    <span id="dirtyFlag">${S.dirty ? '<span class="dirty">● Unsaved changes (Ctrl+S)</span>' : '<span class="saved">All changes saved</span>'}</span>
    <span class="spacer"></span><span id="liveTiming">${timingChip(est.self, est.inst)}</span>
  </div>
  <div class="card">
    <div class="row c2">
      <label>Audience <input data-path="audience" value="${esc(L.audience)}"></label>
      <label>Prerequisites (separate with ;) <input data-path="prerequisites" data-kind="list" value="${esc((L.prerequisites || []).join("; "))}"></label>
    </div>
    <h4>Sub-objectives</h4>
    <ul class="clean">${los.map((lo) => `<li><b>${esc(lo.id)}</b> ${esc(lo.text)} <span class="chip">${esc(lo.bloom || "")}</span></li>`).join("")}</ul>
  </div>
  <h3 style="margin-top:1rem">Sections</h3>
  ${L.sections.map((s, i) => `
    <div class="card sec-card" data-type="${s.type}">
      <div class="sec-head"><b>${i + 1}. <span class="muted small">${esc(s.id)}</span></b>
        <span><button class="btn small" data-act="up" data-i="${i}" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="btn small" data-act="down" data-i="${i}" ${i === L.sections.length - 1 ? "disabled" : ""}>↓</button>
        <button class="btn small danger" data-act="del-sec" data-i="${i}">Delete</button></span></div>
      <div class="row c3">
        <label>Heading <input data-path="sections.${i}.heading" value="${esc(s.heading)}"></label>
        <label>Type <select data-path="sections.${i}.type" data-rerender="1">${TYPES.map((t) => `<option ${t === s.type ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        ${s.type === "activity" ? `<label>Activity minutes <input type="number" min="0" max="30" step="1" data-path="sections.${i}.activity_minutes" data-kind="num" value="${s.activity_minutes || 0}"></label>`
          : `<label title="Which objectives this section covers, e.g. LO1; LO2">Objectives <input data-path="sections.${i}.objectives" data-kind="list" placeholder="${esc(los.map((lo) => lo.id).join("; "))}" value="${esc((s.objectives || []).join("; "))}"></label>`}
      </div>
      ${s.type === "activity" ? `<label title="Which objectives learners practise here — the Teaching check counts practice from this">Objectives practised <input data-path="sections.${i}.objectives" data-kind="list" placeholder="${esc(los.map((lo) => lo.id).join("; "))}" value="${esc((s.objectives || []).join("; "))}"></label>` : ""}
      <label>Content (Markdown · cite sources as [S1])<textarea class="content" data-path="sections.${i}.content_md">${esc(s.content_md)}</textarea></label>
      <div class="sec-foot"><span>${words(s.content_md)} words</span><span>Cites: ${citesOf(s.content_md)}</span></div>
    </div>`).join("")}
  <button class="btn" data-act="add-sec">+ Add section</button>
  <h3 style="margin-top:1.2rem">Knowledge check</h3>
  ${L.quiz.map((q, i) => `
    <div class="card" style="margin-bottom:.6rem">
      <div class="sec-head"><b>Q${i + 1}</b><button class="btn small danger" data-act="del-q" data-i="${i}">Delete</button></div>
      <label>Question <input data-path="quiz.${i}.question" value="${esc(q.question)}"></label>
      ${q.options.map((o, j) => `<div class="opt"><input type="radio" name="ans${i}" data-act="answer" data-i="${i}" data-j="${j}" ${q.answer_index === j ? "checked" : ""} title="Mark correct">
        <input type="text" data-path="quiz.${i}.options.${j}" value="${esc(o)}"></div>`).join("")}
      <div class="row c2">
        <label>Explanation <input data-path="quiz.${i}.explanation" value="${esc(q.explanation)}"></label>
        <label>Assesses objective <select data-path="quiz.${i}.objective">${los.map((lo) => `<option ${lo.id === q.objective ? "selected" : ""}>${lo.id}</option>`).join("")}<option value="" ${q.objective ? "" : "selected"}>—</option></select></label>
      </div>
    </div>`).join("")}
  <button class="btn" data-act="add-q">+ Add question</button>
  ${(L.videos || []).length ? `<h3 style="margin-top:1.2rem">Videos</h3><div class="card">${L.videos.map((v, i) => {
    const src = (S.data.research?.sources || []).find((s) => s.id === v.source_id) || {};
    return `<div class="row c3" style="align-items:end"><div><b>${esc(v.source_id)}</b> <a href="${esc(src.url)}" target="_blank">${esc(src.title)}</a>
      <div class="muted small">${src.duration_sec ? Math.round(src.duration_sec / 6) / 10 + " min · " : ""}${esc(src.channel || "")}</div></div>
      <label>Placement <select data-path="videos.${i}.placement"><option value="optional" ${v.placement === "optional" ? "selected" : ""}>optional (not timed)</option><option value="in_lesson" ${v.placement === "in_lesson" ? "selected" : ""}>in lesson (timed)</option></select></label>
      <button class="btn small danger" data-act="del-video" data-i="${i}">Remove</button></div>
      <label>Why <input data-path="videos.${i}.why" value="${esc(v.why)}"></label>`;
  }).join("<hr>")}</div>` : ""}
  <label style="margin-top:1rem">Instructor notes<textarea rows="3" data-path="instructor_notes">${esc(L.instructor_notes)}</textarea></label>`;
}

function autosize(t) { t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight + 4, 700) + "px"; }

let draftTimer = null;
function markDirty() {
  S.dirty = true;
  const f = $("#dirtyFlag"); if (f) f.innerHTML = '<span class="dirty">● Unsaved changes (Ctrl+S)</span>';
  const b = $('[data-act="save"]'); if (b) b.disabled = false;
  const est = estimate(S.draft); const lt = $("#liveTiming"); if (lt) lt.innerHTML = timingChip(est.self, est.inst);
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {   // crash/refresh safety: keep a local draft of unsaved edits
    try { localStorage.setItem(`lf-draft-${S.id}`, JSON.stringify({ base: S.base, at: new Date().toLocaleString(), content: S.draft })); } catch { /* ignore */ }
  }, 400);
}

document.addEventListener("input", (e) => {
  const el = e.target; const path = el.dataset?.path;
  if (!path || !S.draft) return;
  let v = el.value;
  if (el.dataset.kind === "list") v = v.split(";").map((x) => x.trim()).filter(Boolean);
  if (el.dataset.kind === "num") v = Math.max(0, Math.min(30, +v || 0));
  setPath(S.draft, path, v);
  if (el.tagName === "TEXTAREA") autosize(el);
  markDirty();
  if (el.dataset.rerender) rerenderEditor();
});

function rerenderEditor() { if (S.tab === "editor" && $("#tabBody")) { const y = window.scrollY; $("#tabBody").innerHTML = renderTab(); afterTab(); window.scrollTo(0, y); } }

async function save() {
  if (!S.dirty) return;
  const btn = $('[data-act="save"]'); if (btn) btn.disabled = true;
  try {
    const r = await api(`/api/lessons/${S.id}/content`, { method: "PUT", body: { content: S.draft, base_version: S.base, note: "Instructor edit" } });
    try { localStorage.removeItem(`lf-draft-${S.id}`); } catch { /* ignore */ }
    S.dirty = false;
    toast(`Saved as v${r.version}${r.notes?.length ? " — " + r.notes.join("; ") : ""}`);
    await refresh(true); loadLessons();
  } catch (e) { toast("Save failed: " + e.message + " (your edits are still here and in the browser draft)", true); if (btn) btn.disabled = false; }
}
document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s" && S.tab === "editor") { e.preventDefault(); save(); } });
window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });

/* ----------------------------------------------------------------- sources */
function renderSources() {
  const d = S.data, r = d.research, p = d.plan;
  if (!r) return `<p class="muted">Research has not run yet.</p>`;
  const meta = (s) => s.type === "book" ? `${esc((s.authors || []).join(", "))} · ${esc(s.publisher || "")} ${s.year || ""}`
    : s.type === "video" ? `${esc(s.channel || "")} · ${s.duration_sec ? Math.round(s.duration_sec / 6) / 10 + " min" : ""}${s.views ? " · " + s.views.toLocaleString() + " views" : ""}${s.has_transcript ? " · transcript ✓" : ""}`
    : esc(s.domain || "");
  return `
    ${r.mock ? `<div class="banner warn">Offline demo data — not real research.</div>` : ""}
    ${r.warnings?.length ? `<details class="notes"><summary>${r.warnings.length} search note${r.warnings.length > 1 ? "s" : ""}</summary>${r.warnings.map(esc).join("<br>")}</details>` : ""}
    <div class="tiles">
      <div class="tile"><div class="v">${r.stats.web ?? "–"}</div><div class="k">Web pages</div></div>
      <div class="tile"><div class="v">${r.stats.books ?? "–"}</div><div class="k">Books</div></div>
      <div class="tile"><div class="v">${r.stats.videos ?? "–"}</div><div class="k">YouTube videos</div></div>
      <div class="tile"><div class="v">${r.stats.authoritative ?? "–"}</div><div class="k">Trusted sources</div></div>
    </div>
    <table class="t"><tr><th>ID</th><th>Type</th><th>Source</th><th>Credibility</th><th>Relevance</th></tr>
      ${r.sources.map((s) => `<tr><td><b>${s.id}</b></td><td>${s.type}${s.added_by ? '<br><span class="chip info">added by QC1</span>' : ""}</td>
        <td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a><div class="muted small">${meta(s)}</div></td>
        <td><span class="chip ${s.credibility.tier}">${s.credibility.tier} ${s.credibility.score}</span><div class="muted small">${esc(s.credibility.reason)}</div></td>
        <td>${s.relevance != null ? `<span class="bar"><span style="width:${Math.round((s.relevance || 0) * 100)}%"></span></span>` : ""}</td></tr>`).join("")}
    </table>
    ${p ? `<details class="more"><summary>Research plan</summary><div class="grid2" style="margin-top:.6rem">
      <div class="card"><h4>Research plan — sub-objectives</h4><ul class="clean">${p.sub_objectives.map((lo) => `<li><b>${lo.id}</b> ${esc(lo.text)} <span class="muted small">(${esc(lo.bloom || "")})</span></li>`).join("")}</ul>
        <h4>Misconceptions targeted</h4><ul class="clean">${(p.common_misconceptions || []).map((m) => `<li>${esc(m)}</li>`).join("") || "<li>—</li>"}</ul></div>
      <div class="card"><h4>Search queries</h4>${Object.entries(p.search_queries).map(([k, v]) => `<div><b>${k}:</b> ${v.map(esc).join(" · ")}</div>`).join("")}
        <h4 style="margin-top:.6rem">Key concepts</h4><div>${(p.key_concepts || []).map((c) => `<span class="chip">${esc(c)}</span>`).join(" ")}</div></div>
    </div></details>` : ""}`;
}

/* ---------------------------------------------------------------- QC tabs */
function warnBanner(rep) {
  return rep && rep.warnings && rep.warnings.length
    ? `<div class="banner warn"><b>Partial report:</b> ${rep.warnings.map(esc).join("<br>")}<br><span class="small">Click “Check again” to retry these parts.</span></div>` : "";
}

function staleBanner(rep) {
  if (!rep) return "";
  const latest = S.data.version.version;
  return rep.version !== latest
    ? `<div class="banner info">This report audited <b>v${rep.version}</b>; the latest version is <b>v${latest}</b>. <button class="btn small primary" data-act="rerun-qc">Re-run both QC layers on v${latest}</button></div>` : "";
}

const SOURCE_LABEL = { claim_check: "fact check", consistency_audit: "consistency audit", rubric_review: "teaching review",
  auto: "automatic check", second_opinion: "independent web search" };
const CATEGORY_LABEL = { factual_error: "possible factual error", unsupported_claim: "needs a source", imprecise_claim: "imprecise wording",
  citation: "missing citations", source_reliability: "weak source", source_diversity: "one source dominates", dead_link: "broken link",
  inconsistency: "inconsistency", misleading: "misleading", outdated: "possibly outdated", quiz_answer: "quiz answer", factual_gap: "missing fact",
  alignment: "objective coverage", practice: "practice", assessment: "quiz quality", examples: "examples", clarity: "clarity",
  structure: "structure", engagement: "engagement", pacing: "timing" };

function sectionName(id) {
  if (id === "quiz") return "the knowledge check";
  if (!id) return "the whole lesson";
  const sec = (S.draft?.sections || S.data?.version?.content?.sections || []).find((x) => x.id === id);
  return sec ? `“${sec.heading}”` : id;
}

function whatApplyDoes(i) {
  const where = sectionName(i.section_id);
  return { replace_text: "→ exact text swap", add_citation: "→ adds a source", rewrite_quiz: "→ AI edits quiz",
           add_section: "→ adds a section", manual: "→ fix by hand" }[i.fix?.op] || "→ AI edits this section";
}

function renderIssues(rep, key) {
  const open = rep.issues.filter((i) => i.status === "open");
  const applicable = open.filter((i) => i.fix?.op !== "manual");
  return `<div class="toolbar">
      <h3 style="margin:0">${open.length ? `${open.length} suggestion${open.length > 1 ? "s" : ""} to review` : "Nothing left to review"}</h3>
      <span class="spacer"></span>
      ${applicable.length > 1 ? `<button class="btn" data-act="apply-all" data-report="${key}" title="Apply every open suggestion at once">Apply all ${applicable.length}</button>` : ""}
      <button class="btn small" data-act="rerun-qc" title="Check the latest version again">↻ Check again</button></div>
    ${rep.issues.length ? rep.issues.map((i) => `
      <div class="issue ${i.status} sev-${i.severity}">
        <div><div class="meta"><span class="chip ${i.severity}" title="${{critical: "Likely wrong — check first", major: "Should fix", minor: "Nice to have"}[i.severity] || ""}">${i.severity}</span><span class="chip">${esc(CATEGORY_LABEL[i.category] || i.category)}</span>
          <span class="muted small">in ${esc(sectionName(i.section_id))}</span>
          ${i.status !== "open" ? `<span class="chip ${i.status === "applied" ? "ok" : ""}">${i.status === "applied" ? "✓ applied" + (i.applied_in ? " in v" + i.applied_in : "") : "dismissed"}</span>` : ""}</div>
          <div class="issue-desc">${esc(i.description)}</div>
          ${i.suggestion && i.status === "open" ? `<div class="sug"><b>Suggested:</b> ${esc(i.suggestion)}</div>` : ""}
          ${i.status === "open" && i.fix?.op === "replace_text" && i.fix.find ? `<div class="evid"><del>${esc(i.fix.find)}</del><br><ins>${esc(i.fix.replace)}</ins></div>` : ""}
          ${i.status === "open" ? `<div class="issue-actions">
              <button class="btn small primary" data-act="apply-one" data-report="${key}" data-id="${i.id}" ${i.fix?.op === "manual" ? 'disabled title="Fix this one by hand in Lesson → Edit, then Dismiss it"' : ""}>✓ Apply</button>
              <button class="btn small" data-act="dismiss-one" data-report="${key}" data-id="${i.id}" title="Not needed (wrong, or you fixed it yourself)">✕ Dismiss</button>
              <span class="apply-hint">${esc(whatApplyDoes(i))}</span></div>`
            : i.status === "dismissed" ? `<div class="issue-actions"><button class="btn small ghost" data-act="dismiss-one" data-report="${key}" data-id="${i.id}">↺ Undo</button></div>` : ""}
        </div></div>`).join("") : `<p class="muted">No issues.</p>`}`;
}

function renderQC1() {
  const r = S.data.qc_research;
  if (!r) return `<p class="muted">QC Layer 1 has not run yet.</p>`;
  const m = r.metrics;
  return `${staleBanner(r)}${warnBanner(r)}
    <div class="tiles">
      <div class="tile"><div class="v">${m.grounding_score}%</div><div class="k">Backed by sources</div></div>
      <div class="tile"><div class="v" style="color:${m.factual_errors ? "var(--bad)" : "var(--ok)"}">${m.factual_errors}</div><div class="k">Factual errors</div></div>
      <div class="tile"><div class="v">${m.unsupported}</div><div class="k">Need a source</div></div>
      <div class="tile"><div class="v">${m.authoritative_sources}/${m.sources_total}</div><div class="k">Trusted sources</div></div>
    </div>
    ${renderIssues(r, "qc_research")}
    <details class="more"><summary>All ${r.claims.length} checked claims</summary>
    <table class="t"><tr><th>ID</th><th>Claim</th><th>Evidence</th><th>Expert check</th><th>Notes</th></tr>
    ${r.claims.map((c) => `<tr><td>${c.id}<div class="muted small">${esc(c.section_id)}</div></td><td>${esc(c.text.replace(/\*\*|__|`/g, ""))}</td>
      <td><span class="chip ${c.verdict}">${c.verdict.replace("_", " ")}</span></td><td><span class="chip ${c.knowledge_check}">${c.knowledge_check}</span></td>
      <td>${esc(c.explanation)}${c.second_opinion ? `<div class="evid">Independent re-search: <span class="chip ${c.second_opinion.verdict}">${c.second_opinion.verdict}</span> <a href="${esc(c.second_opinion.url)}" target="_blank">${esc(c.second_opinion.url)}</a></div>` : ""}
        ${c.evidence?.length ? `<details><summary class="small muted">evidence (${c.evidence.length})</summary>${c.evidence.map((e) => `<div class="evid"><b>${e.id}</b> ${esc(e.text)}</div>`).join("")}</details>` : ""}</td></tr>`).join("")}
    </table></details>
    <details class="more"><summary>Source reliability</summary>
    <table class="t"><tr><th>ID</th><th>Source</th><th>Tier</th><th>Cited</th><th>Link</th></tr>
    ${r.sources.map((s) => `<tr><td>${s.id}</td><td><a href="${esc(s.url)}" target="_blank">${esc(s.title)}</a><div class="muted small">${esc(s.reason)}</div></td>
      <td><span class="chip ${s.tier}">${s.tier} ${s.score}</span></td><td>${s.times_cited}×</td>
      <td>${s.link ? `<span class="chip ${s.link.ok ? "ok" : "bad"}">${s.link.ok ? "OK" : "broken"} ${s.link.status || s.link.error || ""}</span>` : '<span class="muted small">n/a</span>'}</td></tr>`).join("")}
    </table></details>`;
}

function renderQC2() {
  const r = S.data.qc_instruction;
  if (!r) return `<p class="muted">QC Layer 2 has not run yet.</p>`;
  const m = r.metrics, a = r.automated;
  return `${staleBanner(r)}${warnBanner(r)}
    <div class="tiles">
      <div class="tile"><div class="v">${m.instructional_score != null ? m.instructional_score + "/100" : "–"}</div><div class="k">Teaching score</div></div>
      <div class="tile"><div class="v">${m.objectives_fully_covered}/${m.objectives_total}</div><div class="k">Objectives covered</div></div>
      <div class="tile"><div class="v">${m.self_paced_minutes}–${m.instructor_led_minutes}</div><div class="k">Minutes (of ${m.budget_minutes})</div></div>
    </div>
    ${renderIssues(r, "qc_instruction")}
    <details class="more"><summary>Rubric, objectives and timing</summary><div class="grid2" style="margin-top:.6rem">
      <div><h3>Rubric</h3>${Object.keys(r.scores).length ? "" : '<p class="muted">Not available this run.</p>'}<table class="t">${Object.entries(r.scores).map(([k, v]) => `<tr><td><b>${k.replace(/_/g, " ")}</b><div class="muted small">${esc(v.justification)}</div></td>
        <td style="white-space:nowrap"><span class="bar"><span style="width:${v.score * 20}%"></span></span> ${v.score}/5</td></tr>`).join("")}</table>
        ${r.strengths?.length ? `<h4 style="margin-top:.8rem">Strengths</h4><ul class="clean">${r.strengths.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}</div>
      <div><h3>Objective traceability</h3><table class="t"><tr><th>Objective</th><th>Taught</th><th>Practised</th><th>Assessed</th></tr>
        ${a.traceability.map((t) => `<tr><td><b>${t.objective}</b> <span class="muted small">${esc(t.text)}</span></td>
          ${["taught", "practised", "assessed"].map((k) => `<td>${t[k].length ? t[k].join(", ") : '<span class="chip bad">missing</span>'}</td>`).join("")}</tr>`).join("")}</table>
        <p class="small">Objective key terms: ${a.objective_terms.map((t) => `<span class="chip ${a.missing_terms.includes(t) ? "bad" : "ok"}">${esc(t)}</span>`).join(" ")}</p>
        <h3>Time budget</h3><table class="t"><tr><th>Segment</th><th>Self-paced</th><th>Instructor-led</th></tr>
        ${a.timing.rows.map((x) => `<tr><td>${esc(x.heading)}</td><td>${x.self_paced}</td><td>${x.instructor_led}</td></tr>`).join("")}
        <tr><th>Total</th><th>${a.timing.self_paced}</th><th>${a.timing.instructor_led}</th></tr></table></div>
    </div></details>`;
}

/* ----------------------------------------------------------- versions tab */
async function loadVersions() {
  let vs;
  try { vs = await api(`/api/lessons/${S.id}/versions`); } catch (e) { $("#versions").textContent = e.message; return; }
  $("#versions").outerHTML = `<div id="versions">
    <p class="muted small">Every AI generation, instructor save and applied QC fix creates a new version. Nothing is overwritten.</p>
    <table class="t"><tr><th>Version</th><th>Author</th><th>Note</th><th>Saved</th><th></th></tr>
    ${vs.map((v, i) => `<tr><td><b>v${v.version}</b>${i === 0 ? ' <span class="chip ok">latest</span>' : ""}</td><td><span class="chip ${v.author === "instructor" ? "info" : v.author === "ai" ? "dark" : "warn"}">${v.author}</span></td>
      <td>${esc(v.note)}</td><td class="small">${esc(v.created_at)}</td>
      <td style="white-space:nowrap"><a class="btn small" target="_blank" href="/api/lessons/${S.id}/preview?version=${v.version}">View</a>
        ${v.version > 1 ? `<button class="btn small" data-act="diff" data-v="${v.version}">Diff vs v${v.version - 1}</button>` : ""}
        ${i > 0 ? `<button class="btn small" data-act="restore" data-v="${v.version}">Restore</button>` : ""}</td></tr>`).join("")}
    </table><div id="diffBox"></div></div>`;
}

/* -------------------------------------------------------------- export tab */
function renderExport() {
  const base = `/api/lessons/${S.id}/export`;
  return `<div class="card">
    ${S.dirty ? '<p class="dirty">Save your edits first to include them.</p>' : ""}
    <p><a class="btn primary" href="${base}?format=zip" title="lesson (.md, .html, .json), references and both QC reports">⬇ Download everything (.zip)</a></p>
    <p class="muted small">Or just the lesson: <a href="${base}?format=html">HTML</a> · <a href="${base}?format=md">Markdown</a> · <a href="${base}?format=json">JSON</a></p>
  </div>`;
}

/* ------------------------------------------------------- action handlers */
document.addEventListener("click", async (e) => {
  const logSum = e.target.closest("details.log > summary");
  if (logSum) { S.logOpen = !logSum.parentElement.open; return; }
  const tabBtn = e.target.closest("[data-tab]");
  if (tabBtn) {
    const fromFooter = !!tabBtn.closest(".flow-footer");
    S.tab = tabBtn.dataset.tab; render(true);
    if (fromFooter) $(".flow")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const el = e.target.closest("[data-act]"); if (!el) return;
  const act = el.dataset.act, i = +el.dataset.i, L = S.draft;
  try {
    switch (act) {
      case "save": return save();
      case "up": case "down": {
        const j = act === "up" ? i - 1 : i + 1;[L.sections[i], L.sections[j]] = [L.sections[j], L.sections[i]];
        markDirty(); return rerenderEditor();
      }
      case "del-sec": if (L.sections.length <= 1) return toast("A lesson needs at least one section", true);
        if (!confirm(`Delete section "${L.sections[i].heading}"?`)) return; L.sections.splice(i, 1); markDirty(); return rerenderEditor();
      case "add-sec": L.sections.push({ id: `sec-${Date.now() % 100000}`, type: "concept", heading: "New section", objectives: [], activity_minutes: 0, content_md: "Write content here. Cite sources like [S1]." });
        markDirty(); return rerenderEditor();
      case "del-q": L.quiz.splice(i, 1); markDirty(); return rerenderEditor();
      case "add-q": L.quiz.push({ id: "", question: "New question?", options: ["Option A", "Option B", "Option C", "Option D"], answer_index: 0, explanation: "", objective: "" });
        markDirty(); return rerenderEditor();
      case "answer": L.quiz[i].answer_index = +el.dataset.j; return markDirty();
      case "del-video": L.videos.splice(i, 1); markDirty(); return rerenderEditor();
      case "restore-draft": { const d = JSON.parse(localStorage.getItem(`lf-draft-${S.id}`)); S.draft = d.content; S.dirty = true;
        if (d.base !== S.data.version.version) toast(`Draft was based on v${d.base}; it will be saved on top of v${S.data.version.version}.`);
        S.base = S.data.version.version; return rerenderEditor(); }
      case "discard-draft": localStorage.removeItem(`lf-draft-${S.id}`); return rerenderEditor();
      case "retry": case "rerun":
        if (act === "rerun" && !confirm(`Re-run "${S.data.steps[el.dataset.step].label}" and every step after it? Existing versions are kept.`)) return;
        await api(`/api/lessons/${S.id}/retry`, { method: "POST", body: { step: el.dataset.step } });
        toast("Running…"); return refresh();
      case "rerun-qc": await api(`/api/lessons/${S.id}/qc/run`, { method: "POST" }); toast("Re-running both QC layers…"); return refresh();
      case "apply-one": case "apply-all": {
        const rep = S.data[el.dataset.report];
        const ids = act === "apply-one" ? [el.dataset.id] : rep.issues.filter((x) => x.status === "open" && x.fix?.op !== "manual").map((x) => x.id);
        if (act === "apply-all" && !confirm(`Apply all ${ids.length} suggestions? Each change is saved as a new version you can undo from History.`)) return;
        if (S.dirty && !confirm("You have unsaved edits. Fixes apply to the latest SAVED version and your unsaved edits will be discarded from the editor (still in browser draft). Continue?")) return;
        document.querySelectorAll('[data-act^="apply"],[data-act^="dismiss"]').forEach((b) => { b.disabled = true; });
        el.disabled = true; el.innerHTML = '<span class="spinner"></span> Applying…';
        const r = await api(`/api/lessons/${S.id}/qc/apply`, { method: "POST", body: { report: el.dataset.report, issue_ids: ids } });
        const failed = r.results.filter((x) => !x.ok);
        toast(failed.length ? `Couldn't apply ${failed.length} suggestion(s): ${failed.map((f) => f.message).join("; ").slice(0, 200)}` : `Applied — saved as v${r.version}.`, failed.length > 0);
        S.dirty = false; loadLessons(); return refresh(true);
      }
      case "dismiss-one": await api(`/api/lessons/${S.id}/qc/dismiss`, { method: "POST", body: { report: el.dataset.report, issue_ids: [el.dataset.id] } });
        return refresh();
      case "diff": {
        const v = +el.dataset.v; const r = await api(`/api/lessons/${S.id}/diff?a=${v - 1}&b=${v}`);
        $("#diffBox").innerHTML = `<h4 style="margin-top:1rem">Changes v${v - 1} → v${v}</h4><div class="diff">${r.diff ? r.diff.split("\n").map((l) =>
          `<span class="${l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}">${esc(l) || " "}</span>`).join("\n") : "No differences."}</div>`;
        return;
      }
      case "restore": if (!confirm(`Restore v${el.dataset.v}? It will be copied as a new latest version.`)) return;
        await api(`/api/lessons/${S.id}/versions/${el.dataset.v}/restore`, { method: "POST" }); toast("Restored"); return refresh(true);
      case "delete": if (!confirm("Delete this lesson and all its versions?")) return;
        await api(`/api/lessons/${S.id}`, { method: "DELETE" }); S.id = null; S.data = null;
        $("#main").innerHTML = `<section class="win"><div class="win-title t-purple"><span>Deleted</span><span class="win-btns" aria-hidden="true"><i class="wb-min"></i><i class="wb-max"></i><i class="wb-close"></i></span></div><div class="win-body empty"><h2>Lesson deleted</h2></div></section>`; return loadLessons();
    }
  } catch (err) { toast(err.message, true); if (act.startsWith("apply")) refresh(); }
});

// the dialog's close box really closes it
document.addEventListener("click", (e) => { if (e.target.closest(".toast .wb-close")) $("#toast").classList.add("hidden"); });

/* ------------------------------------------------------------------- boot */
(async function boot() {
  await loadHealth();
  await loadLessons();
  if (S.lessons.length) openLesson(S.lessons[0].id);
  setInterval(loadLessons, 10000);
})();
