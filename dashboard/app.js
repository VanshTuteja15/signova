// SIGNOVA dashboard: vanilla ES module, no build step. Talks to the FastAPI server over
// REST (/api/*) and one WebSocket (/ws) that streams status, transcript, gloss, step, done,
// error, telemetry and eval events.

import { Hand3D } from "./hand3d.js";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const el = (tag, props = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...kids.filter((k) => k !== null && k !== undefined));
  return e;
};

const SAMPLES = ["I love you", "Code is so cool", "No, I have three", "The wild idea is bold", "Lisa is so wise", "Four or five", "Hello, I love you"];

const S = {
  app: null,
  joints: [],
  jointInfo: [],
  rest: {},
  library: [],
  byId: {},
  transport: null,
  claude: { available: false },
  hands: {},
  gloss: null,
  studio: { frames: [], cur: 0, loaded: "", requires: null, sending: false, pending: null, lastSend: 0 },
  cal: [],
  evalDone: false,
};

// ------------------------------------------------------------------ helpers
async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new Error("Can't reach the SIGNOVA server. Is it still running?");
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return data;
}

function toast(msg, kind = "info", ms = 6000) {
  const t = el("div", { class: `toast ${kind}`, role: kind === "bad" ? "alert" : "status", text: msg });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

function log(text, cls = "sys") {
  const box = $("#log");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
  box.append(el("div", { class: cls, text }));
  while (box.childNodes.length > 400) box.firstChild.remove();
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function setStatus(node, msg, kind = "") {
  node.textContent = msg;
  node.classList.remove("bad", "good");
  if (kind) node.classList.add(kind);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ theme + tabs
function initTheme() {
  $("#theme-btn").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("signova-theme", root.dataset.theme); } catch { /* private mode */ }
  });
}

function initTabs() {
  const tabs = $$('[role="tab"]');
  const show = (tab, focus = false) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      $("#" + t.getAttribute("aria-controls")).hidden = !on;
    }
    if (focus) tab.focus();
    if (tab.id === "tab-cal") refreshPorts();
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => show(t));
    t.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") show(tabs[(i + 1) % tabs.length], true);
      if (e.key === "ArrowLeft") show(tabs[(i - 1 + tabs.length) % tabs.length], true);
    });
  });
  S.showTab = (id) => show($("#" + id));
}

// ------------------------------------------------------------------ state + header
async function loadState() {
  const st = await api("/api/state");
  S.app = st;
  S.jointInfo = st.hand.joints;
  S.joints = st.hand.joints.map((j) => j.name);
  S.rest = st.rest;
  S.transport = st.transport;
  S.claude = st.claude;
  setLibrary(st.library);
  $("#speed").value = String(st.settings.speed);
  if (!$("#speed").value) $("#speed").value = "1";
  $("#save-rec").checked = st.settings.save_recordings;
  $("#disclaimer").textContent = st.disclaimer;
  renderHeader();
  renderPrivacy();
  return st;
}

function setLibrary(lib) {
  S.library = lib;
  S.byId = Object.fromEntries(lib.map((s) => [s.id, s]));
  renderLibrary();
  refreshStudioLoad();
}

function renderHeader() {
  const t = S.transport || {};
  const pill = $("#hand-pill");
  pill.classList.remove("ok", "warn", "bad");
  let text = t.mode || "hand";
  if (t.mode === "sim") { text = "simulation"; pill.classList.add("warn"); }
  else if (t.connected) { text = `${t.mode === "emulator" ? "emulator" : t.port} · connected`; pill.classList.add(t.mismatch ? "bad" : "ok"); }
  else { text = `${t.mode} · ${t.reconnecting ? "reconnecting" : "disconnected"}`; pill.classList.add("bad"); }
  pill.querySelector("span").textContent = text;
  pill.title = t.detail || "";
  $("#sim-banner").hidden = t.mode !== "sim";
  $("#conn-detail").textContent = t.detail || t.mode || "—";
  const ai = $("#ai-pill");
  const stt = $("#stt-engine").value;
  const cloud = $("#gloss-engine").value === "claude" && S.claude.available;
  ai.querySelector("span").textContent = `${stt} · ${cloud ? "Claude (cloud)" : "rules"}`;
  ai.classList.toggle("cloud", cloud);
  ai.title = cloud ? `Gloss by ${S.claude.model} via the Anthropic API (text only, no audio leaves the laptop)` : "All processing on this laptop";
}

function renderPrivacy() {
  const saving = $("#save-rec").checked;
  const note = $("#privacy-note");
  note.textContent = saving ? "recordings ARE being saved (testing)" : "audio processed in memory, not saved";
  note.style.color = saving ? "var(--warn)" : "";
  const cn = $("#claude-note");
  if ($("#gloss-engine").value === "claude") {
    cn.hidden = false;
    cn.textContent = S.claude.available
      ? `Claude (${S.claude.model}) runs in the cloud: the transcript text is sent to the Anthropic API. Its output is checked by the validator.`
      : `Claude is not available (${S.claude.detail}). The rule-based gloss will be used instead.`;
  } else cn.hidden = true;
}

// ------------------------------------------------------------------ websocket
function connectWS(retry = 0) {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  const pill = $("#ws-pill");
  let keepAlive = null;
  ws.onopen = () => {
    pill.classList.remove("bad");
    pill.classList.add("ok");
    pill.querySelector("span").textContent = "server online";
    keepAlive = setInterval(() => ws.readyState === 1 && ws.send("ping"), 20000);
    if (retry > 0) loadState().catch(() => {});
    retry = 0;
  };
  ws.onmessage = (m) => {
    try { handleEvent(JSON.parse(m.data)); } catch (e) { console.warn("bad event", e); }
  };
  ws.onclose = () => {
    clearInterval(keepAlive);
    pill.classList.remove("ok");
    pill.classList.add("bad");
    pill.querySelector("span").textContent = "server offline";
    setTimeout(() => connectWS(retry + 1), Math.min(5000, 500 * 2 ** retry));
  };
}

function handleEvent(ev) {
  switch (ev.type) {
    case "status": return onStatus(ev);
    case "transcript": return onTranscript(ev);
    case "gloss": S.gloss = ev; return renderGloss(ev);
    case "step": return onStep(ev);
    case "done": return onDone(ev);
    case "error":
      toast(ev.message, "bad");
      log(`! ${ev.message}`, "err");
      return;
    case "telemetry": return onTelemetry(ev);
    case "eval": return onEvalEvent(ev);
    default: return;
  }
}

function onStatus(ev) {
  if (ev.transport) { S.transport = ev.transport; renderHeader(); }
  if (ev.state === "relaxed" && ev.pose && S.hands.live) S.hands.live.animateTo(ev.pose, 600);
  if (ev.state === "library") api("/api/library").then(setLibrary).catch(() => {});
  if (ev.state === "settings" && ev.settings) {
    $("#save-rec").checked = ev.settings.save_recordings;
    renderPrivacy();
  }
  if (["transcribing", "speech", "performing"].includes(ev.state)) setStatus($("#talk-status"), ev.detail || "");
  if (ev.state === "performing") $("#now-sub").textContent = ev.detail || "performing";
  if (ev.detail && ev.state !== "performing") log(`# ${ev.detail}`, "sys");
}

function onTranscript(ev) {
  const p = $("#transcript");
  p.textContent = "";
  if (!ev.text) { p.append(el("span", { class: "muted", text: "(no speech recognised)" })); return; }
  p.append(document.createTextNode(`“${ev.text}”`));
  const meta = ev.source === "mic" ? ` · ${ev.engine}, ${Math.round(ev.stt_ms)} ms` : " · typed";
  p.append(el("small", { class: "muted", text: meta }));
}

function renderGloss(g) {
  const box = $("#gloss");
  box.textContent = "";
  g.items.forEach((it, i) => {
    const tok = el("span", { class: `tok ${it.type}`, "data-i": i });
    if (it.type === "sign") { tok.textContent = it.id; tok.title = `“${it.word}”`; if (S.byId[it.id]?.draft) tok.title += " · draft (not yet validated by a signer)"; }
    else if (it.type === "fs") tok.textContent = "FS:" + it.word.toUpperCase();
    else {
      tok.textContent = it.word;
      if (it.type === "skip") tok.append(el("small", { text: it.reason || "" }));
      if (it.type === "drop") tok.title = "ASL doesn't sign this word";
    }
    box.append(tok);
  });
  if (!g.items.length) box.append(el("span", { class: "muted", text: "Nothing to sign." }));
  const engine = g.engine === "claude" ? `Claude (${g.model})` : g.fallback ? "rules (fallback)" : "rules";
  $("#gloss-engine-label").textContent = `${engine} · ${Math.round(g.latency_ms)} ms`;
  $("#gloss-note").textContent = g.note + (g.rejected ? ` (${g.rejected} rejected)` : "");
}

function onStep(ev) {
  const masked = ev.context === "evaluation";
  $$("#gloss .tok").forEach((t) => t.classList.toggle("now", !masked && Number(t.dataset.i) === ev.item_index));
  $("#now-sign").textContent = masked ? "?" : ev.label;
  $("#now-sub").textContent = masked ? "evaluation: answer hidden" : ev.kind === "bounce" ? `${ev.context} · doubled letter` : ev.context;
  if (S.hands.live) S.hands.live.animateTo(ev.pose, ev.move_ms);
}

function onDone(ev) {
  $$("#gloss .tok").forEach((t) => t.classList.remove("now"));
  if (ev.ok) {
    $("#now-sub").textContent = ev.steps ? `done · ${(ev.total_ms / 1000).toFixed(1)} s` : "nothing to sign";
    setStatus($("#talk-status"), "Ready.");
  } else {
    $("#now-sub").textContent = ev.reason || "stopped";
    if (ev.message) setStatus($("#talk-status"), ev.message, "bad");
  }
}

function onTelemetry(ev) {
  if (ev.kind === "serial") log(`${ev.dir === "tx" ? "→" : ev.dir === "rx" ? "←" : " "} ${ev.line}`, ev.dir);
  else if (ev.kind === "latency") {
    const parts = [`speech end → first pose ${Math.round(ev.first_pose_ms)} ms`];
    if (ev.stt_ms != null) parts.push(`STT ${Math.round(ev.stt_ms)} ms`);
    if (ev.gloss_ms != null) parts.push(`gloss ${Math.round(ev.gloss_ms)} ms`);
    if (ev.median_ms != null) parts.push(`median ${Math.round(ev.median_ms)} ms`);
    $("#latency").textContent = parts.join(" · ");
  } else if (ev.kind === "device") log(`# ${ev.detail}`, "sys");
}

// ------------------------------------------------------------------ live tab
function buildTelemetry() {
  const tele = $("#tele");
  tele.textContent = "";
  S.teleRows = {};
  for (const j of S.jointInfo) {
    const bar = el("div", { class: "bar", role: "meter", "aria-label": j.label, "aria-valuemin": 0, "aria-valuemax": 1 }, el("i"));
    const v = el("span", { class: "v", text: "0.00" });
    tele.append(el("span", { class: "nm", text: j.label }), bar, v);
    S.teleRows[j.name] = { bar, fill: bar.firstChild, v };
  }
}

let telePending = null;
function paintTele(pose, moving) {
  telePending = { pose: { ...pose }, moving: new Set(moving) };
  if (paintTele.raf) return;
  paintTele.raf = requestAnimationFrame(() => {
    paintTele.raf = null;
    const { pose: p, moving: m } = telePending;
    for (const [name, row] of Object.entries(S.teleRows || {})) {
      const x = p[name] ?? 0;
      row.fill.style.width = `${(x * 100).toFixed(1)}%`;
      row.v.textContent = x.toFixed(2);
      row.bar.setAttribute("aria-valuenow", x.toFixed(2));
      row.bar.classList.toggle("moving", m.has(name));
    }
  });
}

function initLive() {
  buildTelemetry();
  S.hands.live = new Hand3D($("#live-stage"), {
    joints: S.joints,
    rest: S.rest,
    fullRangeMs: S.app.hand.limits.full_range_ms,
    onUpdate: paintTele,
  });
  S.hands.live.setPose(S.app.current_pose || S.rest);

  for (const s of SAMPLES) {
    $("#samples").append(el("button", { class: "chip", type: "button", text: s, onclick: () => { $("#say-text").value = s; say(); } }));
  }
  $("#say-form").addEventListener("submit", (e) => { e.preventDefault(); say(); });
  $("#speed").addEventListener("change", () => api("/api/settings", { method: "POST", body: { speed: Number($("#speed").value) } }).catch((e) => toast(e.message, "bad")));
  $("#save-rec").addEventListener("change", () => {
    renderPrivacy();
    api("/api/settings", { method: "POST", body: { save_recordings: $("#save-rec").checked } }).catch((e) => toast(e.message, "bad"));
  });
  $("#gloss-engine").addEventListener("change", () => { renderHeader(); renderPrivacy(); });
  $("#stt-engine").addEventListener("change", () => {
    renderHeader();
    const v = S.app.speech?.vosk;
    if ($("#stt-engine").value === "vosk" && v && !v.model_present) toast("The Vosk model isn't downloaded yet: run `signova download-models`.", "warn");
  });
  $("#stop-btn").addEventListener("click", () => api("/api/stop", { method: "POST" }).catch((e) => toast(e.message, "bad")));
  $("#relax-btn").addEventListener("click", () => api("/api/relax", { method: "POST" }).catch((e) => toast(e.message, "bad")));
  $("#log-clear").addEventListener("click", () => { $("#log").textContent = ""; });
  initTalk();
}

async function say() {
  const text = $("#say-text").value.trim();
  if (!text) return;
  const btn = $("#say-btn");
  btn.disabled = true;
  try {
    await api("/api/say", { method: "POST", body: { text, engine: $("#gloss-engine").value } });
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.disabled = false;
  }
}

// Hold-to-talk with MediaRecorder (works on http://localhost / 127.0.0.1).
function initTalk() {
  const btn = $("#talk-btn");
  const status = $("#talk-status");
  const talk = { stream: null, rec: null, chunks: [], t0: 0, tEnd: 0, busy: false };

  const ui = (mode) => {
    btn.classList.toggle("recording", mode === "rec");
    btn.classList.toggle("busy", mode === "busy");
    $("#talk-label").textContent = mode === "rec" ? "Listening… release to sign" : mode === "busy" ? "Working…" : "Hold to talk";
  };

  async function start() {
    if (talk.rec || talk.busy) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setStatus(status, "This browser can't record audio here. Use Chrome on http://127.0.0.1:8000, or type instead.", "bad");
      return;
    }
    try {
      talk.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      const denied = e && (e.name === "NotAllowedError" || e.name === "SecurityError");
      setStatus(status, denied
        ? "Microphone permission was denied. Click the icon at the left of Chrome's address bar → Microphone → Allow, or type instead."
        : "No microphone found. Plug one in, or type instead.", "bad");
      return;
    }
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
    talk.chunks = [];
    talk.rec = new MediaRecorder(talk.stream, mime ? { mimeType: mime } : undefined);
    talk.rec.ondataavailable = (e) => { if (e.data.size) talk.chunks.push(e.data); };
    talk.rec.onstop = send;
    talk.rec.start();
    talk.t0 = performance.now();
    ui("rec");
    setStatus(status, "Listening…");
  }

  function stop() {
    if (!talk.rec) return;
    talk.tEnd = Date.now();
    talk.dur = performance.now() - talk.t0;
    talk.rec.stop();
    talk.rec = null;
    talk.busy = true;
    ui("busy");
  }

  async function send() {
    talk.stream?.getTracks().forEach((t) => t.stop()); // release the mic between recordings
    talk.stream = null;
    const blob = new Blob(talk.chunks, { type: talk.chunks[0]?.type || "audio/webm" });
    try {
      if (talk.dur < 300 || blob.size < 800) {
        setStatus(status, "That was too short. Hold the button while you speak.", "bad");
        return;
      }
      const form = new FormData();
      form.append("audio", blob, "speech.webm");
      form.append("engine", $("#stt-engine").value);
      form.append("gloss_engine", $("#gloss-engine").value);
      form.append("auto_sign", "true");
      form.append("t_end", String(talk.tEnd));
      setStatus(status, "Transcribing…");
      const r = await api("/api/transcribe", { method: "POST", form });
      if (r.text) {
        $("#say-text").value = r.text;
        setStatus(status, `Heard “${r.text}”`, "good");
      } else setStatus(status, "No speech recognised. Speak closer to the microphone and try again.", "bad");
    } catch (e) {
      setStatus(status, e.message, "bad");
    } finally {
      talk.busy = false;
      ui("idle");
    }
  }

  btn.addEventListener("pointerdown", (e) => { e.preventDefault(); btn.setPointerCapture(e.pointerId); start(); });
  btn.addEventListener("pointerup", stop);
  btn.addEventListener("pointercancel", stop);
  btn.addEventListener("click", (e) => { if (e.detail === 0) (talk.rec ? stop() : start()); }); // keyboard Enter toggles
  const typing = (t) => t && (t.closest("input, textarea, select, [contenteditable]") || (t.tagName === "BUTTON" && t !== btn));
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || e.repeat || typing(e.target) || $("#panel-live").hidden) return;
    e.preventDefault();
    start();
  });
  document.addEventListener("keyup", (e) => {
    if (e.code !== "Space" || typing(e.target) || $("#panel-live").hidden) return;
    e.preventDefault();
    stop();
  });
}

// ------------------------------------------------------------------ pose studio
function refreshStudioLoad() {
  const sel = $("#studio-load");
  if (!sel) return;
  const keep = sel.value;
  sel.textContent = "";
  sel.append(el("option", { value: "", text: "— new sign —" }));
  for (const s of S.library) sel.append(el("option", { value: s.id, text: `${s.id} (${s.kind}, tier ${s.tier}${s.available ? "" : ", unavailable"})` }));
  sel.value = S.byId[keep] ? keep : "";
}

function studioPose() {
  return S.studio.frames[S.studio.cur].pose;
}

function renderFrames() {
  const tabs = $("#frame-tabs");
  tabs.textContent = "";
  S.studio.frames.forEach((_, i) => {
    tabs.append(el("button", {
      type: "button", role: "tab", "aria-selected": String(i === S.studio.cur), text: `Frame ${i + 1}`,
      onclick: () => { S.studio.cur = i; renderFrames(); syncSliders(); S.hands.studio?.setPose(studioPose()); },
    }));
  });
  $("#frame-del").disabled = S.studio.frames.length <= 1;
  const f = S.studio.frames[S.studio.cur];
  $("#fr-hold").value = f.hold_ms ?? "";
  $("#fr-move").value = f.move_ms ?? "";
}

function buildSliders() {
  const box = $("#sliders");
  box.textContent = "";
  S.sliders = {};
  for (const j of S.jointInfo) {
    const id = `sl-${j.name}`;
    const input = el("input", { type: "range", id, min: 0, max: 1, step: 0.01, value: 0 });
    const out = el("output", { for: id, text: "0.00" });
    input.addEventListener("input", () => {
      const v = Number(input.value);
      studioPose()[j.name] = v;
      out.textContent = v.toFixed(2);
      S.hands.studio?.setPose(studioPose());
      sendLivePose();
    });
    box.append(el("label", { for: id, text: j.label }), input, out);
    S.sliders[j.name] = { input, out };
  }
}

function syncSliders() {
  const pose = studioPose();
  for (const [name, s] of Object.entries(S.sliders)) {
    const v = pose[name] ?? S.rest[name] ?? 0;
    s.input.value = String(v);
    s.out.textContent = Number(v).toFixed(2);
  }
}

// Throttled (~20 Hz, latest wins) live pose to the real/emulated hand.
async function sendLivePose() {
  if (!$("#studio-live").checked) return;
  S.studio.pending = { ...studioPose() };
  if (S.studio.sending) return;
  S.studio.sending = true;
  try {
    while (S.studio.pending) {
      const wait = 50 - (performance.now() - S.studio.lastSend);
      if (wait > 0) await sleep(wait);
      const pose = S.studio.pending;
      S.studio.pending = null;
      S.studio.lastSend = performance.now();
      const body = { pose: Object.fromEntries(S.joints.map((j) => [j, pose[j] ?? S.rest[j]])), ms: 60 };
      await api("/api/pose", { method: "POST", body });
      S.hands.live?.setPose(body.pose);
    }
  } catch (e) {
    toast(e.message, "bad");
    $("#studio-live").checked = false;
  } finally {
    S.studio.sending = false;
  }
}

function loadIntoStudio(id) {
  const s = S.byId[id];
  S.studio.loaded = s ? id : "";
  S.studio.requires = s ? s.requires : null;
  $("#st-id").value = s ? s.id : "";
  $("#st-kind").value = s ? s.kind : "word";
  $("#st-tier").value = String(s ? s.tier : 2);
  $("#st-english").value = s ? s.english.join(", ") : "";
  $("#st-notes").value = s ? s.notes : "";
  $("#st-validated").checked = s ? s.validated_by_signer : false;
  $("#st-reviewer").value = s?.reviewer || "";
  $("#st-date").value = s?.validated_on || "";
  S.studio.frames = s
    ? s.frames.map((f) => ({ pose: { ...f.raw, ...f.pose }, hold_ms: f.hold_ms, move_ms: f.move_ms }))
    : [{ pose: { ...S.rest }, hold_ms: null, move_ms: null }];
  S.studio.cur = 0;
  $("#st-requires").textContent = s
    ? `Required joints: ${s.requires.join(", ") || "none"}${s.available ? "" : ` — unavailable on this hand (${s.unavailable_reason})`}`
    : "Required joints: worked out from the pose when you save.";
  $("#studio-load").value = S.studio.loaded;
  renderFrames();
  syncSliders();
  S.hands.studio?.setPose(studioPose());
}

function initStudio() {
  buildSliders();
  S.hands.studio = new Hand3D($("#studio-stage"), { joints: S.joints, rest: S.rest, fullRangeMs: S.app.hand.limits.full_range_ms });
  loadIntoStudio("");
  $("#studio-load").addEventListener("change", (e) => loadIntoStudio(e.target.value));
  $("#frame-add").addEventListener("click", () => {
    S.studio.frames.push({ pose: { ...studioPose() }, hold_ms: null, move_ms: null });
    S.studio.cur = S.studio.frames.length - 1;
    renderFrames(); syncSliders();
  });
  $("#frame-dup").addEventListener("click", () => {
    const f = S.studio.frames[S.studio.cur];
    S.studio.frames.splice(S.studio.cur + 1, 0, { pose: { ...f.pose }, hold_ms: f.hold_ms, move_ms: f.move_ms });
    S.studio.cur += 1;
    renderFrames(); syncSliders();
  });
  $("#frame-del").addEventListener("click", () => {
    if (S.studio.frames.length <= 1) return;
    S.studio.frames.splice(S.studio.cur, 1);
    S.studio.cur = Math.max(0, S.studio.cur - 1);
    renderFrames(); syncSliders(); S.hands.studio.setPose(studioPose());
  });
  const num = (v) => (v === "" ? null : Math.max(0, Math.round(Number(v))));
  $("#fr-hold").addEventListener("input", (e) => { S.studio.frames[S.studio.cur].hold_ms = num(e.target.value); });
  $("#fr-move").addEventListener("input", (e) => { S.studio.frames[S.studio.cur].move_ms = num(e.target.value); });
  $("#studio-rest").addEventListener("click", () => {
    S.studio.frames[S.studio.cur].pose = { ...studioPose(), ...S.rest };
    syncSliders(); S.hands.studio.setPose(studioPose()); sendLivePose();
  });
  $("#studio-play").addEventListener("click", async () => {
    for (const f of S.studio.frames) {
      await S.hands.studio.animateTo(f.pose, f.move_ms ?? S.app.hand.timing.move_ms);
      await sleep(f.hold_ms ?? S.app.hand.timing.word_hold_ms);
    }
  });
  $("#studio-perform").addEventListener("click", async () => {
    const id = ($("#st-id").value || "").trim().toUpperCase();
    if (!S.byId[id]) { setStatus($("#st-status"), "Save the sign first, then perform it.", "bad"); return; }
    try { await api(`/api/sign/${encodeURIComponent(id)}`, { method: "POST" }); setStatus($("#st-status"), `Performing ${id} on the hand.`, "good"); }
    catch (e) { setStatus($("#st-status"), e.message, "bad"); }
  });
  $("#st-validated").addEventListener("change", (e) => {
    if (e.target.checked && !$("#st-date").value) $("#st-date").value = new Date().toISOString().slice(0, 10);
  });
  $("#st-save").addEventListener("click", saveSign);
  const del = $("#st-delete");
  del.addEventListener("click", async () => {
    const id = ($("#st-id").value || "").trim().toUpperCase();
    if (!S.byId[id]) { setStatus($("#st-status"), "Nothing to delete: this sign is not in the library.", "bad"); return; }
    if (del.dataset.armed !== id) {
      del.dataset.armed = id;
      del.textContent = `Click again to delete ${id}`;
      setTimeout(() => { del.dataset.armed = ""; del.textContent = "Delete sign"; }, 4000);
      return;
    }
    try {
      await api(`/api/library/${encodeURIComponent(id)}`, { method: "DELETE" });
      setLibrary(await api("/api/library"));
      loadIntoStudio("");
      setStatus($("#st-status"), `Deleted ${id}. A backup of the previous file is in signs/library.yaml.bak.`, "good");
    } catch (e) { setStatus($("#st-status"), e.message, "bad"); }
    del.dataset.armed = ""; del.textContent = "Delete sign";
  });
}

async function saveSign() {
  const status = $("#st-status");
  const id = ($("#st-id").value || "").trim().toUpperCase();
  if (!/^[A-Z0-9_]{1,16}$/.test(id)) { setStatus(status, "Sign ID must be 1–16 characters: letters, digits or _.", "bad"); return; }
  const body = {
    kind: $("#st-kind").value,
    tier: Number($("#st-tier").value),
    english: $("#st-english").value.split(",").map((s) => s.trim()).filter(Boolean),
    notes: $("#st-notes").value.trim(),
    validated_by_signer: $("#st-validated").checked,
    reviewer: $("#st-reviewer").value.trim() || null,
    validated_on: $("#st-date").value || null,
    frames: S.studio.frames.map((f) => {
      const fr = { pose: Object.fromEntries(Object.entries(f.pose).map(([k, v]) => [k, Math.round(Number(v) * 1000) / 1000])) };
      if (f.hold_ms != null) fr.hold_ms = f.hold_ms;
      if (f.move_ms != null) fr.move_ms = f.move_ms;
      return fr;
    }),
  };
  // Keep an existing sign's required joints; let the server work them out for new signs.
  if (S.studio.loaded === id && S.studio.requires) body.requires = S.studio.requires;
  try {
    const r = await api(`/api/library/${encodeURIComponent(id)}`, { method: "PUT", body });
    setLibrary(await api("/api/library"));
    loadIntoStudio(r.sign.id);
    const phrase = r.sign.english[0];
    setStatus(status, `Saved ${r.sign.id}.${phrase ? ` It is now in the gloss vocabulary: try “${phrase}” on the Live tab.` : ""}`, "good");
  } catch (e) {
    setStatus(status, e.message, "bad");
  }
}

// ------------------------------------------------------------------ calibration
async function refreshPorts() {
  $("#mode-select").value = S.transport?.mode || "sim";
  try {
    const ports = await api("/api/ports");
    const sel = $("#port-select");
    const keep = sel.value;
    sel.textContent = "";
    sel.append(el("option", { value: "", text: "auto (first ESP32-looking port)" }));
    for (const p of ports) sel.append(el("option", { value: p.device, text: `${p.device} — ${p.description}${p.likely_esp32 ? " (ESP32?)" : ""}` }));
    sel.value = keep;
  } catch (e) { setStatus($("#conn-status"), e.message, "bad"); }
}

function initCalibration() {
  $("#ports-refresh").addEventListener("click", refreshPorts);
  $("#connect-btn").addEventListener("click", async () => {
    const mode = $("#mode-select").value;
    const btn = $("#connect-btn");
    btn.disabled = true;
    setStatus($("#conn-status"), mode === "sim" ? "Switching to simulation…" : "Connecting…");
    try {
      S.transport = await api("/api/transport", { method: "POST", body: { mode, port: $("#port-select").value || null } });
      renderHeader();
      setStatus($("#conn-status"), `Connected: ${S.transport.detail}`, "good");
      if (S.transport.mismatch) toast(S.transport.mismatch, "bad", 12000);
      await loadCalibration();
    } catch (e) {
      setStatus($("#conn-status"), e.message, "bad");
    } finally {
      btn.disabled = false;
    }
  });
  $("#cal-load").addEventListener("click", loadCalibration);
  loadCalibration();
}

function calRange() {
  return (S.transport?.driver === "feetech") ? { lo: 0, hi: 1023, unit: "position counts", label: "Feetech · position counts" } : { lo: 500, hi: 2500, unit: "µs", label: "PCA9685 · microseconds" };
}

async function loadCalibration() {
  try {
    const r = await api("/api/calibration");
    S.cal = r.cal;
    renderCalibration();
  } catch (e) {
    $("#cal-table").textContent = "";
    $("#cal-table").append(el("p", { class: "note warn", text: `Calibration unavailable: ${e.message}` }));
  }
}

function renderCalibration() {
  const rng = calRange();
  $("#cal-units").textContent = rng.label;
  const table = $("#cal-table");
  table.textContent = "";
  for (const c of S.cal) {
    const info = S.jointInfo.find((j) => j.name === c.joint);
    const draft = { ...c };
    const mid = Math.round((c.min + c.max) / 2);
    const id = `raw-${c.joint}`;
    const slider = el("input", { type: "range", id, min: rng.lo, max: rng.hi, step: 1, value: mid, "aria-label": `${c.joint} raw servo command` });
    const out = el("output", { for: id, text: String(mid) });
    const vals = el("div", { class: "vals" });
    const showVals = (dirty = false) => {
      vals.textContent = `ch ${c.ch} · min ${draft.min} · max ${draft.max} · rest ${Number(draft.rest).toFixed(2)} · invert ${draft.inv ? "yes" : "no"}${dirty ? " · unsaved" : ""}`;
    };
    showVals();
    let rawTimer = null;
    const sendRaw = (v) => {
      clearTimeout(rawTimer);
      rawTimer = setTimeout(() => api("/api/raw", { method: "POST", body: { joint: c.joint, us: v } }).catch((e) => toast(e.message, "bad")), 80);
    };
    slider.addEventListener("input", () => { out.textContent = slider.value; sendRaw(Number(slider.value)); });
    const inv = el("input", { type: "checkbox" });
    inv.checked = !!c.inv;
    inv.addEventListener("change", () => { draft.inv = inv.checked; showVals(true); });
    const btn = (text, fn, cls = "btn ghost small") => el("button", { class: cls, type: "button", text, onclick: fn });
    const acts = el("div", { class: "acts" },
      btn("Set min", () => { draft.min = Number(slider.value); showVals(true); }),
      btn("Set max", () => { draft.max = Number(slider.value); showVals(true); }),
      btn("Set rest here", () => {
        let u = (Number(slider.value) - draft.min) / Math.max(1, draft.max - draft.min);
        u = Math.min(1, Math.max(0, draft.inv ? 1 - u : u));
        draft.rest = Math.round(u * 100) / 100;
        showVals(true);
      }),
      el("label", { class: "check" }, inv, document.createTextNode("Invert")),
      btn("Test sweep", async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        try {
          const seq = [];
          for (let v = draft.min; v <= draft.max; v += Math.max(1, Math.round((draft.max - draft.min) / 30))) seq.push(v);
          for (const v of [...seq, ...seq.reverse()]) {
            slider.value = String(v); out.textContent = String(v);
            await api("/api/raw", { method: "POST", body: { joint: c.joint, us: v } });
            await sleep(60);
          }
        } catch (err) { toast(err.message, "bad"); } finally { b.disabled = false; }
      }),
      btn("Save to ESP32", async () => {
        try {
          const r = await api("/api/calibration", { method: "POST", body: { joint: c.joint, min: draft.min, max: draft.max, inv: draft.inv, rest: draft.rest } });
          S.cal = r.cal;
          toast(`Saved calibration for ${c.joint}.`, "info", 3000);
          renderCalibration();
        } catch (err) { toast(err.message, "bad"); }
      }, "btn primary small"),
    );
    table.append(el("div", { class: "cal-row" }, el("span", { class: "name", text: info?.label || c.joint }), slider, out, acts, vals));
  }
}

// ------------------------------------------------------------------ evaluation
function evalUI({ started = false, waiting = false, finished = false } = {}) {
  $("#ev-next").disabled = !started || waiting || finished;
  $("#ev-repeat").disabled = !waiting;
  $("#ev-guess").disabled = !waiting;
  $("#ev-answer").disabled = !waiting;
}

function initEval() {
  evalUI();
  $("#ev-start").addEventListener("click", async () => {
    const tiers = $$(".ev-tier").filter((c) => c.checked).map((c) => Number(c.value));
    try {
      const p = await api("/api/eval/start", {
        method: "POST",
        body: { count: Number($("#ev-count").value) || 10, tiers, randomize: $("#ev-random").checked, participant: $("#ev-participant").value.trim() },
      });
      $("#ev-results").hidden = true;
      $("#ev-progress").textContent = `0 of ${p.total} answered`;
      setStatus($("#ev-status"), "Session started. Press “Perform next sign” when the signer is watching the hand.");
      evalUI({ started: true });
    } catch (e) { setStatus($("#ev-status"), e.message, "bad"); }
  });
  $("#ev-next").addEventListener("click", async () => {
    try {
      const p = await api("/api/eval/next", { method: "POST" });
      $("#ev-progress").textContent = `sign ${p.trial} of ${p.total}`;
      setStatus($("#ev-status"), `Sign ${p.trial} is being performed. Type what the signer saw.`);
      evalUI({ started: true, waiting: true });
      $("#ev-guess").value = "";
      $("#ev-guess").focus();
    } catch (e) { setStatus($("#ev-status"), e.message, "bad"); }
  });
  $("#ev-repeat").addEventListener("click", () => api("/api/eval/repeat", { method: "POST" }).catch((e) => toast(e.message, "bad")));
  $("#ev-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const conf = $('input[name="conf"]:checked')?.value;
    try {
      const p = await api("/api/eval/answer", { method: "POST", body: { guess: $("#ev-guess").value, confidence: conf ? Number(conf) : null } });
      $("#ev-progress").textContent = `${p.answered} of ${p.total} answered`;
      if (p.finished) {
        evalUI({ started: true, finished: true });
        setStatus($("#ev-status"), "Session finished.", "good");
        await showEvalResults(p.saved);
      } else {
        evalUI({ started: true });
        setStatus($("#ev-status"), "Answer recorded. Press “Perform next sign”.");
      }
    } catch (err) { setStatus($("#ev-status"), err.message, "bad"); }
  });
}

async function showEvalResults(saved) {
  const s = await api("/api/eval/summary");
  if (!s.revealed) return;
  $("#ev-results").hidden = false;
  $("#ev-overall").textContent = s.overall_pct == null ? "—" : `${s.overall_pct}%`;
  const tb = $("#ev-table tbody");
  tb.textContent = "";
  for (const r of s.per_sign) tb.append(el("tr", {}, el("td", { text: r.sign_id }), el("td", { text: r.shown }), el("td", { text: r.correct }), el("td", { text: `${r.recognition_pct}%` })));
  if (saved) $("#ev-saved").textContent = `Saved on this laptop: ${saved.trials} and ${saved.summary}`;
}

function onEvalEvent(ev) {
  if (ev.state === "finished" && !$("#ev-results").hidden) return;
  if (ev.total) $("#ev-progress").textContent = `${ev.answered} of ${ev.total} answered`;
}

// ------------------------------------------------------------------ library
function renderLibrary() {
  const grid = $("#lib");
  if (!grid) return;
  const filter = $("#lib-filter").value;
  grid.textContent = "";
  const list = S.library.filter((s) => filter === "all" || (filter === "available" ? s.available : s.draft));
  for (const s of list) {
    const badges = el("div", {},
      s.available ? null : el("span", { class: "badge na", text: "unavailable" }),
      document.createTextNode(" "),
      s.draft ? el("span", { class: "badge draft", text: "draft" }) : el("span", { class: "badge ok", text: `validated${s.reviewer ? " · " + s.reviewer : ""}` }),
    );
    const card = el("article", { class: `sign-card${s.available ? "" : " unavailable"}`, "aria-label": `Sign ${s.id}` },
      el("div", { class: "id", text: s.id }),
      el("div", { class: "meta", text: `${s.kind} · tier ${s.tier} · ${s.frames.length} frame${s.frames.length > 1 ? "s" : ""}` }),
      badges,
      s.english.length ? el("div", { class: "eng", text: s.english.map((e) => `“${e}”`).join(", ") }) : null,
      s.available ? null : el("div", { class: "meta", text: s.unavailable_reason }),
      el("div", { class: "actions" },
        el("button", {
          class: "btn ghost small", type: "button", text: "Preview", disabled: !s.available,
          onclick: async () => { try { S.showTab("tab-live"); await api(`/api/sign/${encodeURIComponent(s.id)}`, { method: "POST" }); } catch (e) { toast(e.message, "bad"); } },
        }),
        el("button", { class: "btn ghost small", type: "button", text: "Edit", onclick: () => { loadIntoStudio(s.id); S.showTab("tab-studio"); } }),
      ),
    );
    grid.append(card);
  }
  if (!list.length) grid.append(el("p", { class: "note", text: "No signs match this filter." }));
}

// ------------------------------------------------------------------ boot
async function boot() {
  initTheme();
  initTabs();
  $("#lib-filter").addEventListener("change", renderLibrary);
  for (;;) {
    try {
      await loadState();
      break;
    } catch (e) {
      setStatus($("#talk-status"), `${e.message} Retrying…`, "bad");
      await sleep(2000);
    }
  }
  initLive();
  initStudio();
  initCalibration();
  initEval();
  connectWS();
  log("# dashboard ready", "sys");
}

boot();
