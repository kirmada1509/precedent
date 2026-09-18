"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const UNTRUSTED = new Set(["email_body", "external_tool_output"]);

const S = {
  cards: new Map(), order: [], transcript: [], stats: null, selected: null,
  tickets: {}, running: false, gemini: false, activeTicket: null, lastSeq: 0,
  rechecks: new Set(), rechecked: new Map(), // rechecks: card ids created by a re-run; rechecked: original id -> new id
};

const ms = (x) => (x == null ? "—" : x < 10 ? x.toFixed(1) : x < 1000 ? String(Math.round(x)) : (x / 1000).toFixed(1) + "k");
const msUnit = (x) => (x == null ? "—" : x >= 1000 ? (x / 1000).toFixed(1) + " s" : ms(x) + " ms");
const pct = (x) => Math.round((x || 0) * 100) + "%";

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

let toastTimer;
function toast(html) {
  const t = $("#toast");
  t.innerHTML = html;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 4200);
}

/* ---------- event handling ---------- */
function handle(type, data, live) {
  if (type === "card" || type === "recheck") {
    const card = data.card;
    if (type === "recheck") { S.rechecks.add(card.card_id); S.rechecked.set(data.from, card.card_id); }
    const seen = S.cards.has(card.card_id); // a re-run is announced twice: as a card, then as a recheck
    S.cards.set(card.card_id, card);
    if (!seen) S.order.unshift(card.card_id);
    if (data.stats) S.stats = { ...(S.stats || {}), ...data.stats };
    if (live && type === "card") {
      const cur = S.cards.get(S.selected);
      if (card.decision.verdict !== "allow" && (!cur || cur.decision.verdict === "allow")) S.selected = card.card_id;
    }
    if (type === "recheck") S.selected = card.card_id;
    const call = [...S.transcript].reverse().find((e) => e.kind === "call" && e.status === "pending" && e.tool === card.decision.action.tool && !e.card_id);
    if (call) call.card_id = card.card_id;
  } else if (type === "agent") {
    if (data.kind === "say") S.transcript.push({ kind: "say", text: data.text });
    else if (data.kind === "propose") {
      S.transcript.push({ kind: "call", tool: data.tool, args: data.args, reason: data.reason, status: "pending" });
      if (data.tool === "read_ticket") S.activeTicket = data.args.ticket_id;
    } else {
      const call = [...S.transcript].reverse().find((e) => e.kind === "call" && e.status === "pending" && e.tool === data.tool);
      if (call) {
        call.card_id = data.card_id;
        call.status = data.kind === "result" ? "ok" : data.verdict === "block" ? "blocked" : "escalated";
        call.out = data.kind === "result" ? "ran" : data.why;
      }
    }
  } else if (type === "adjudication") {
    const c = S.cards.get(data.card_id);
    if (c) c.adjudication = data;
    if (S.stats) S.stats.index_docs = data.index_docs;
    if (live) toast(`Decision recorded. Live in the index in <b>${ms(data.insert_to_queryable_ms)} ms</b>. Re-run the action to see it enforced.`);
  } else if (type === "reset") {
    S.cards.clear(); S.order = []; S.transcript = []; S.selected = null; S.activeTicket = null; S.rechecks.clear(); S.rechecked.clear();
    refreshStats();
  } else if (type === "run") {
    S.running = data.state === "started";
    if (data.state === "started" && live) { S.selected = null; S.activeTicket = null; }
  } else if (type === "killswitch") {
    $("#kill").checked = data.active;
    $(".kill").classList.toggle("on", data.active);
  }
  if (live) renderAll();
}

async function refreshStats() {
  try { S.stats = await api("/v1/stats"); } catch (_) {}
  renderKPIs(); renderProof();
}

/* ---------- rendering ---------- */
function renderAll() { renderKPIs(); renderInbox(); renderTranscript(); renderQueue(); renderStream(); renderEvidence(); renderProof(); renderControls(); }

function renderKPIs() {
  const s = S.stats || {};
  const c = s.check_ms || {};
  const k = (cls, v, l) => `<div class="kpi ${cls}"><b>${v}</b><span>${l}</span></div>`;
  $("#kpis").innerHTML =
    k("", s.checked ?? 0, "checked") + k("allow", s.allow ?? 0, "allowed") + k("block", s.block ?? 0, "blocked") +
    k("esc", s.escalate ?? 0, "escalated") + k("", c.n ? ms(c.p50) + " ms" : "—", "p50 check") +
    k("", c.n ? ms(c.p99) + " ms" : "—", "p99 check") + k("", s.index_docs ?? "—", "precedents");
}

function renderControls() {
  $("#btn-run").disabled = S.running;
  $("#btn-variant").disabled = S.running;
  $("#btn-gemini").disabled = S.running;
  $("#btn-gemini").hidden = !S.gemini;
  $("#run-state").textContent = S.running ? "running" : S.transcript.length ? "finished" : "idle";
}

function renderInbox() {
  $("#inbox").innerHTML = Object.entries(S.tickets).map(([id, t]) => {
    let body = esc(t.body);
    if (t.hidden && t.body.includes(t.hidden)) {
      const i = t.body.indexOf(t.hidden);
      body = esc(t.body.slice(0, i)) + `<span class="hidden-text">${esc(t.hidden)}</span><span class="hint">hidden, white on white</span>`;
    }
    return `<div class="ticket ${S.activeTicket === id ? "active" : ""}"><div class="t-head"><span class="t-id">${esc(id)}</span><span class="t-sub">${esc(t.subject)}</span></div><div class="t-body">${body}</div></div>`;
  }).join("");
}

function argsStr(args) {
  return Object.entries(args || {}).map(([k, v]) => `${k}=${typeof v === "string" && v.length > 28 ? v.slice(0, 26) + "…" : v}`).join(" ");
}

function renderTranscript() {
  const el = $("#transcript");
  const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
  el.innerHTML = S.transcript.map((e) => {
    if (e.kind === "say") return `<div class="say">${esc(e.text)}</div>`;
    const out = e.status === "pending" ? `<span class="spin"></span> checking against precedent…` : e.status === "ok" ? "checked · allowed · ran" : esc(e.out);
    return `<div class="call ${e.status}"><div class="c-top"><code><b>${esc(e.tool)}</b></code><span class="c-args">${esc(argsStr(e.args))}</span></div><div class="c-out">${out}</div></div>`;
  }).join("") || `<div class="none">Nothing yet. Press “Run support shift”.</div>`;
  if (stick) el.scrollTop = el.scrollHeight;
}

function rowHTML(c, fresh) {
  const d = c.decision, a = d.action, isRe = S.rechecks.has(c.card_id);
  return `<button class="row v-${d.verdict} ${S.selected === c.card_id ? "sel" : ""} ${fresh ? "fresh" : ""}" data-id="${c.card_id}">
    <span class="dot"></span>
    <span class="what"><b>${esc(a.tool)}</b><span>${esc(argsStr(a.args))}</span>${isRe ? '<span class="tag">re-run</span>' : ""}</span>
    <span class="lat">${ms(d.latency_ms)} ms</span>
    <span class="pill ${d.verdict}">${d.verdict.toUpperCase()}</span></button>`;
}

let renderedRows = new Set();
function renderStream() {
  const el = $("#stream");
  el.innerHTML = S.order.map((id) => rowHTML(S.cards.get(id), !renderedRows.has(id))).join("") || `<div class="none">Every proposed action will appear here with its check time.</div>`;
  S.order.forEach((id) => renderedRows.add(id));
}

function renderQueue() {
  const pend = S.order.map((id) => S.cards.get(id)).filter((c) => c.decision.verdict !== "allow" && !c.adjudication && !c.decision.fail_closed && !S.rechecks.has(c.card_id));
  $("#queue-count").textContent = pend.length;
  $("#queue-count").classList.toggle("zero", !pend.length);
  $("#queue").innerHTML = pend.map((c) => rowHTML(c, false)).join("") || `<div class="none">Nothing waiting. Blocked and escalated actions land here for a human decision.</div>`;
}

function parseText(t) {
  const o = {};
  (t || "").split(" | ").forEach((p) => { const i = p.indexOf("="); if (i > 0) o[p.slice(0, i)] = p.slice(i + 1); });
  return o;
}

function evidenceHTML(c) {
  const d = c.decision, a = d.action, v = d.verdict, f = parseText(d.text);
  const absent = (f.args || "").split(",").filter((t) => t.endsWith(":ABSENT")).map((t) => t.split(":")[0]);
  const argRows = Object.entries(a.args).map(([k, val]) => {
    const src = a.arg_prov[k] || "agent_derived", u = UNTRUSTED.has(src);
    return `<tr><td>${esc(k)}</td><td>${esc(val)}</td><td><span class="src ${u ? "untrusted" : ""}">${esc(src)}${u ? " · untrusted" : ""}</span></td></tr>`;
  }).join("") + absent.map((k) => `<tr><td>${esc(k)}</td><td>not supplied</td><td><span class="src absent">expected · absent</span></td></tr>`).join("");
  const checks = Object.entries(a.checks || {}).map(([k, val]) => `<span class="chip ${val ? "" : "hot"}">${esc(k)}: ${val ? "yes" : "no"}</span>`).join("");
  const matched = (c.matched_features || []).map((m) => `<span class="chip ${/UNTRUSTED|ABSENT|:no/.test(m) ? "hot" : "hit"}">${esc(m)}</span>`).join("");
  const differs = (c.differing_features || []).map((m) => `<span class="chip">${esc(m)}</span>`).join("");
  const precs = (d.precedents || []).map((p) => {
    const ctx = (parseText(p.text).ctx || "").slice(0, 60);
    return `<tr><td><span class="pill ${p.verdict}">${p.verdict.toUpperCase()}</span></td>
      <td>${esc(p.reason_code || "approved")}${p.source === "human" ? '<span class="badge-h">HUMAN</span>' : ""}<div class="small" style="margin:0">${esc(ctx)}</div></td>
      <td class="score">${p.score.toFixed(3)}</td>
      <td><span class="wbar"><i style="width:${Math.round((p.weight || 0) * 100)}%"></i></span>${pct(p.weight)}</td></tr>`;
  }).join("");

  let banner = "";
  if (c.previous_verdict) {
    banner = `<div class="banner">Re-run after the human decision. Before: <b>${c.previous_verdict.toUpperCase()}</b> (${pct(c.previous_p_block)} blocked-weight). Now: <b>${v.toUpperCase()}</b> (${pct(d.p_block)}). The new precedent is in the table below.</div>`;
  }

  let action = "";
  if (!c.adjudication && v !== "allow" && !d.fail_closed && !S.rechecks.has(c.card_id)) {
    action = `<div class="review"><h4>Human review</h4>
      <div class="small" style="margin:0 0 6px">Your decision is written into the live index and applies to the very next call.</div>
      <input id="note" placeholder="Optional note for the audit trail" maxlength="120">
      <div class="btns">
        <button class="btn danger" data-adj="block">Confirm: block this</button>
        <button class="btn good" data-adj="allow">Actually: allow this</button></div></div>`;
  } else if (c.adjudication) {
    const done = S.rechecked.get(c.card_id);
    action = `<div class="done">Recorded as <b>${esc(c.adjudication.verdict.toUpperCase())}</b> (${esc(c.adjudication.reason_code)}). Queryable in <b>${ms(c.adjudication.insert_to_queryable_ms)} ms</b>, no reindex job.
      <div class="btns" style="margin-top:8px">${done ? `<button class="btn" data-open="${done}">View the re-run</button>` : `<button class="btn primary" data-recheck="${c.card_id}">Re-run this action</button>`}</div></div>`;
  }

  return `${banner}
  <div class="ev-head"><span class="pill ${v}">${v.toUpperCase()}</span><h3>${esc(c.headline)}</h3></div>
  <div class="metrics"><span>check <b>${ms(d.latency_ms)} ms</b></span><span>Moss query <b>${ms(d.moss_ms)} ms</b></span>
    <span>${v === "escalate" ? "agreement" : "confidence"} <b>${d.reason_code === "no_precedent" || d.reason_code === "novel_shape" ? "—" : pct(d.confidence)}</b></span><span>budget <b>${d.within_budget ? "met" : "exceeded"}</b></span></div>
  <div class="sect"><h4>Proposed action</h4><div class="mono" style="margin-bottom:6px"><b>${esc(a.tool)}</b></div>
    <table class="args">${argRows || '<tr><td colspan="3">no arguments</td></tr>'}</table>
    ${checks ? `<div class="chips" style="margin-top:8px">${checks}</div>` : ""}
    <div class="small">Agent's stated intent: “${esc(a.context)}”. Argument sources are traced by the harness, not reported by the agent.</div></div>
  ${matched ? `<div class="sect"><h4>Matched features</h4><div class="chips">${matched}</div>${differs ? `<div class="chips" style="margin-top:6px">${differs}</div>` : ""}</div>` : ""}
  <div class="sect"><h4>Nearest precedents (${(d.precedents || []).length})</h4><div class="small" style="margin:0 0 6px">${esc(d.reason)}</div>
    ${precs ? `<table class="prec"><thead><tr><th></th><th>Past decision</th><th>Similarity</th><th>Weight</th></tr></thead><tbody>${precs}</tbody></table>` : `<div class="none">No precedent for this tool: nothing to reason from, so Precedent does not guess.</div>`}
    <div class="small">Moss similarity scores are noisy (about ±0.03), so the decision uses relative weight and exact-shape matches, not a score cut-off.</div></div>
  ${action}
  <div class="hash">card ${esc(c.card_id)} · sha256 ${esc(c.sha256.slice(0, 32))}… · ${esc(c.created_at)}</div>`;
}

function renderEvidence() {
  const c = S.cards.get(S.selected);
  if (!c) return;
  $("#evidence").innerHTML = evidenceHTML(c);
}

function renderProof() {
  const s = S.stats || {}, b = s.bench || {}, e = s.eval || {}, m = b.moss, h = b.moss_hosted, j = b.llm_judge;
  const lap = m?.burst_1000?.whole_check_ms, lapSp = m?.spaced_300ms_100?.whole_check_ms;
  const hb = h?.burst_1000?.whole_check_ms, hs = h?.spaced_300ms_100?.whole_check_ms, judge = j?.latency_ms;
  const head = hb || lap;
  const vals = [lap?.p50, lapSp?.p50, hb?.p50, hs?.p50].filter((x) => x != null);
  const max = Math.max(judge?.p50 || 0, ...vals, 1);
  const bar = (label, val, color) => `<div class="bar"><span>${label}</span><div class="track"><div class="fill" style="width:${Math.max(1.2, (val / max) * 100)}%;background:${color}"></div></div><span class="num">${msUnit(val)}</span></div>`;
  const t1 = head ? `<div class="tile"><h2>Cost of checking one action</h2>
      <div class="big">${ms(head.p50)} ms<small>p50 · p99 ${ms(head.p99)} ms · ${hb ? "deployed host" : "laptop"}, 1,000 checks</small></div>
      <div class="bars">${lap ? bar("Laptop", lap.p50, "var(--accent)") : ""}${hb ? bar("Deployed host", hb.p50, "var(--accent)") : ""}${hs ? bar("Host, paced calls", hs.p50, "var(--accent)") : lapSp ? bar("Laptop, paced", lapSp.p50, "var(--accent)") : ""}${j ? bar("LLM judge", judge.p50, "var(--block)") : ""}</div>
      ${j && vals.length ? `<p>One agent run of 40 actions, every action checked: <b>${((Math.min(...vals) * 40) / 1000).toFixed(1)}–${((Math.max(...vals) * 40) / 1000).toFixed(1)} s</b> with Precedent vs <b>${Math.round((judge.p50 * 40) / 1000)} s</b> with a fast LLM judge (${esc(j.model)}, p50 ${msUnit(judge.p50)}, measured from the laptop on a busy day).</p>` : ""}
      <p>${h ? esc(h.machine) + ". " : ""}${esc(m?.machine || "")}. ${(h || m).index_docs} precedents, hybrid retrieval, in-process.</p></div>` : `<div class="tile"><h2>Cost of checking one action</h2><p>Run <code>eval/bench_latency.py</code> to populate.</p></div>`;
  const t2 = e.n ? `<div class="tile"><h2>Held-out evaluation · ${e.n} unseen actions</h2>
      <div class="big">${Math.round(e.strict_accuracy * e.n)}/${e.n}<small>correct</small></div>
      <ul><li>Attacks blocked <b>${e.attacks_blocked}/${e.attacks_total}</b>, none allowed through</li>
      <li>Novel tools and shapes escalated <b>${e.novel_escalated}/${e.novel_total}</b>, never guessed</li>
      <li>Benign false blocks <b>${e.benign_false_block}/${e.benign_total}</b></li>
      <li>A static “block untrusted” rule misses <b>${e.baseline_static?.attacks_missed_allowed ?? "?"}/${e.attacks_total}</b> of the attacks</li>
      <li>Decisions unchanged across <b>${e.stability?.repeats ?? "?"}</b> repeated retrievals (Moss scores are noisy)</li></ul>
      <p>Synthetic data. A simple same-shape majority vote scores about the same here: most of the signal is explicit features; retrieval adds ranking, evidence and live adaptation.</p></div>` : `<div class="tile"><h2>Held-out evaluation</h2><p>Run <code>eval/eval_heldout.py</code> to populate.</p></div>`;
  const wb = (h || m)?.writeback_insert_to_retrievable_ms;
  const t3 = `<div class="tile"><h2>Human decision to enforced</h2>
      <div class="big">${wb ? ms(wb.p50) + " ms" : "—"}<small>p50 insert → retrievable${wb ? ` · n=${wb.n}` : ""}${h ? " · deployed host" : ""}</small></div>
      <p>${s.writeback_ms?.last != null ? `This session: last write-back <b>${ms(s.writeback_ms.last)} ms</b>. ` : ""}A review decision goes straight into the live in-process index. There is no reindex job, so the next call already sees it.</p>
      <p>Fail-closed: if the lookup errors or exceeds its ${s.budget_ms ?? 50} ms budget, the action is blocked, never allowed.</p></div>`;
  $("#proof").innerHTML = t1 + t2 + t3;
}

/* ---------- interactions ---------- */
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-id],[data-adj],[data-recheck],[data-open]");
  if (!t) return;
  try {
    if (t.dataset.id) { S.selected = t.dataset.id; renderStream(); renderQueue(); renderEvidence(); }
    else if (t.dataset.open) { S.selected = t.dataset.open; renderAll(); }
    else if (t.dataset.adj) {
      t.disabled = true;
      await api("/v1/adjudicate", { card_id: S.selected, verdict: t.dataset.adj, note: ($("#note")?.value || "").trim() });
      renderAll();
    } else if (t.dataset.recheck) {
      t.disabled = true;
      const r = await api(`/v1/cards/${t.dataset.recheck}/recheck`, {});
      S.selected = r.card_id;
      renderAll();
    }
  } catch (e) { toast(esc(e.message)); if (t) t.disabled = false; }
});

async function run(scenario) {
  try { await api("/v1/demo/run", { scenario, pace: 0.7 }); } catch (e) { toast(esc(e.message)); }
}
$("#btn-run").onclick = () => run("shift");
$("#btn-variant").onclick = () => run("variant");
$("#btn-gemini").onclick = () => run("gemini");
$("#btn-reset").onclick = async () => { renderedRows = new Set(); await api("/v1/demo/reset", {}); };
$("#kill").onchange = (e) => api("/v1/killswitch", { active: e.target.checked }).catch((x) => toast(esc(x.message)));

/* ---------- boot ---------- */
async function boot() {
  const snap = await api("/v1/state");
  S.tickets = snap.tickets; S.stats = snap.stats; S.gemini = snap.gemini; S.running = snap.running;
  snap.events.forEach((e) => { S.lastSeq = Math.max(S.lastSeq, e.seq); handle(e.type, e.data, false); });
  renderedRows = new Set(S.order);
  const first = [...S.order].reverse().find((id) => S.cards.get(id).decision.verdict !== "allow" && !S.rechecks.has(id));
  if (!S.selected && first) S.selected = first;
  renderAll();
  const es = new EventSource("/v1/stream");
  ["card", "agent", "adjudication", "recheck", "reset", "run", "killswitch"].forEach((t) =>
    es.addEventListener(t, (m) => {
      const seq = Number(m.lastEventId || 0);
      if (seq && seq <= S.lastSeq) return;
      S.lastSeq = Math.max(S.lastSeq, seq);
      handle(t, JSON.parse(m.data), true);
    }));
}
boot().catch((e) => { $("#evidence").innerHTML = `<div class="empty"><p class="big">Cannot reach the service</p><p>${esc(e.message)}</p></div>`; });
