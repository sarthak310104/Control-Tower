/*
 * Control Tower dashboard: shared core.
 * API client, a polling store over the coordinator's REST API, helpers, and
 * the effect bus the 3D scenes listen to. Classic script (no build step);
 * everything hangs off window.CT.
 *
 * Every agent-supplied string (names, notes, messages, file paths, code) is
 * rendered with textContent via el(), never innerHTML: one agent must not be
 * able to inject markup into the page another person is watching.
 */
"use strict";
window.CT = window.CT || {};

(() => {
  const POLL_MS = 2000;
  const PALETTE = ["#9fd8ff", "#ff8fb4", "#7ef0c4", "#ffc46b", "#d4ff6b", "#c7b3ff", "#7fe3ff", "#ffa98f"];

  /* ---------------- helpers ---------------- */
  const $ = (q, root = document) => root.querySelector(q);
  function el(tag, props = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k.startsWith("--")) n.style.setProperty(k, v);
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat(Infinity)) if (k != null && k !== false) n.append(k instanceof Node ? k : String(k));
    return n;
  }
  function dur(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
  }
  const clock = (t) => new Date(t).toTimeString().slice(0, 5);
  const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
  const word = (n) => WORDS[n] ?? String(n);
  const storage = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* unavailable */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* unavailable */ } },
  };

  /* ---------------- API ---------------- */
  class ApiError extends Error {
    constructor(status, detail) {
      const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail?.message || `request failed (${status})`;
      super(msg); this.status = status; this.detail = detail;
    }
  }
  /* The admin token, when the coordinator has auth on. Kept in sessionStorage so it
     goes away with the tab; never put in a URL. */
  const auth = {
    get token() { try { return sessionStorage.getItem("ct.admin"); } catch { return null; } },
    set token(v) { try { v ? sessionStorage.setItem("ct.admin", v) : sessionStorage.removeItem("ct.admin"); } catch { /* unavailable */ } },
  };
  async function api(path, { method = "GET", body, params } = {}) {
    const url = new URL(path, location.origin);
    if (params) for (const [k, v] of Object.entries(params)) if (v != null) url.searchParams.set(k, v);
    const headers = {};
    if (body) headers["Content-Type"] = "application/json";
    if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
    const resp = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await resp.json(); } catch { /* empty */ }
    if (resp.status === 401) emit("unauthorized", data?.detail);
    if (!resp.ok) throw new ApiError(resp.status, data?.detail);
    return data;
  }

  /* ---------------- event bus ---------------- */
  const listeners = {};
  const on = (name, fn) => ((listeners[name] ||= []).push(fn), () => (listeners[name] = listeners[name].filter((f) => f !== fn)));
  const emit = (name, data) => (listeners[name] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });

  /* ---------------- store ---------------- */
  const store = {
    online: false, health: { claim_ttl_seconds: 600 },
    projects: [], projectId: null, joinKeys: {},
    agents: [], claims: [], files: [], events: [], messages: [],
    contents: {}, // file_path -> {updated_at, source, symbols}
    eventCursor: 0, messageCursor: 0, loaded: false,
    get ttl() { return (this.health.claim_ttl_seconds || 600) * 1000; },
    get project() { return this.projects.find((p) => p.id === this.projectId) || null; },
    get isDemo() { return this.project?.name === this.health.demo_project; },
  };
  const ts = (iso) => (iso ? Date.parse(iso) : null);

  function normClaim(c) {
    return {
      id: c.id, agent: c.agent_id, file: c.file_path, symbol: c.symbol_name, line_start: c.line_start, line_end: c.line_end,
      note: c.note, status: c.status, created: ts(c.created_at), beat: ts(c.last_heartbeat_at), released: ts(c.released_at), release_note: c.release_note,
    };
  }
  const active = () => store.claims.filter((c) => c.status === "active");
  const agentById = (id) => store.agents.find((a) => a.id === id);
  const agentName = (id) => agentById(id)?.name ?? (id == null ? "system" : `agent#${id}`);
  function agentColor(id) {
    const i = store.agents.findIndex((a) => a.id === id);
    return PALETTE[(i < 0 ? (id || 0) : i) % PALETTE.length];
  }
  const agentState = (a) => (Date.now() - ts(a.last_seen_at) < 120_000 ? "active" : Date.now() - ts(a.last_seen_at) < store.ttl ? "quiet" : "gone");
  const target = (c) => c.symbol || `lines ${c.line_start}–${c.line_end}`;
  const refusals = () => store.events.filter((e) => e.type === "claim_rejected");
  const editFindings = () => store.events.filter((e) => e.type === "edit_unclaimed" || e.type === "edit_conflict");

  /* Current line range of a claim, re-resolved against the latest snapshot (claims are by name). */
  function rangeOf(c) {
    if (c.symbol) {
      const s = store.contents[c.file]?.symbols?.find((x) => x.symbol === c.symbol);
      if (s) return [s.line_start, s.line_end];
    }
    return [c.line_start, c.line_end];
  }
  function lineCount(file) { return store.files.find((f) => f.file_path === file)?.line_count || store.contents[file]?.source.split("\n").length || 1; }

  async function loadContent(file, force) {
    const meta = store.files.find((f) => f.file_path === file);
    const have = store.contents[file];
    if (have && !force && (!meta || have.updated_at === meta.updated_at)) return have;
    try {
      const c = await api("/files/content", { params: { project_id: store.projectId, file_path: file } });
      store.contents[file] = c; emit("content", file); return c;
    } catch { return null; }
  }

  let pollTimer = null, pollToken = 0;
  async function selectProject(id) {
    store.projectId = id ? Number(id) : null;
    if (id) storage.set("ct.project", String(id)); else storage.del("ct.project");
    Object.assign(store, { agents: [], claims: [], files: [], events: [], messages: [], contents: {}, eventCursor: 0, messageCursor: 0, loaded: false });
    pollToken++;
    emit("project", store.projectId);
    emit("change");
    await poll();
  }

  async function loadProjects() {
    store.projects = await api("/projects");
    emit("projects");
    return store.projects;
  }

  async function poll() {
    clearTimeout(pollTimer);
    const token = pollToken;
    try {
      if (!store.loaded && !store.healthLoaded) { store.health = await api("/health"); store.healthLoaded = true; }
      if (store.projectId) {
        const pid = store.projectId;
        const [agents, claims, files, events, messages] = await Promise.all([
          api("/agents", { params: { project_id: pid } }),
          api("/claims", { params: { project_id: pid } }),
          api("/files", { params: { project_id: pid } }),
          api("/activity", { params: { project_id: pid, since_id: store.eventCursor, limit: 500 } }),
          api("/messages", { params: { project_id: pid, since_id: store.messageCursor, limit: 500 } }),
        ]);
        if (token !== pollToken) return;
        store.agents = agents.sort((a, b) => a.id - b.id);
        store.claims = claims.map(normClaim);
        store.files = files;
        const first = !store.loaded;
        if (events.length) {
          store.eventCursor = events.at(-1).id;
          const fresh = events.map((e) => ({ ...e, at: ts(e.created_at), isNew: !first }));
          store.events = store.events.concat(fresh).slice(-1000);
          if (!first) fresh.forEach((e) => emit("event", e));
        }
        if (messages.length) {
          store.messageCursor = messages.at(-1).id;
          store.messages = store.messages.concat(messages.map((m) => ({ ...m, at: ts(m.created_at), isNew: !first }))).slice(-500);
        }
        await Promise.all(files.map((f) => loadContent(f.file_path)));
        if (token !== pollToken) return;
        store.loaded = true;
      }
      if (!store.online) { store.online = true; emit("online", true); }
      emit("change");
    } catch (err) {
      if (store.online || !store.loaded) { store.online = false; emit("online", false); }
      console.warn("poll failed", err);
    } finally {
      if (token === pollToken) pollTimer = setTimeout(poll, POLL_MS);
    }
  }
  const refresh = () => poll();

  /* ---------------- actions (same REST calls an agent's MCP server makes) ---------------- */
  async function claim({ agentId, file, symbol, start, end, note, source }, dry = false) {
    const src = source ?? store.contents[file]?.source;
    if (src == null) throw new ApiError(0, `no content for ${file} yet`);
    const body = { project_id: store.projectId, agent_id: agentId, file_path: file, source: src, symbol_name: symbol || null, line_start: symbol ? null : start, line_end: symbol ? null : end, note: note || null };
    try {
      const r = await api(dry ? "/claims/check" : "/claims", { method: "POST", body });
      if (!dry) await refresh();
      return dry ? { check: r } : { claim: normClaim(r) };
    } catch (err) {
      if (err.status === 409 && typeof err.detail === "object") { await refresh(); return { refused: err.detail }; }
      throw err;
    }
  }
  async function release(claimId, agentId, note) {
    await api(`/claims/${claimId}/release`, { method: "POST", body: { agent_id: agentId, release_note: note || null } });
    await refresh();
  }
  async function send(from, to, body) {
    await api("/messages", { method: "POST", body: { project_id: store.projectId, from_agent_id: from, to_agent_id: to, body } });
    await refresh();
  }
  async function register(name, role) {
    const a = await api("/agents", { method: "POST", body: { project_id: store.projectId, name, role: role || null } });
    await refresh(); return a;
  }
  async function createProject(name) {
    const p = await api("/projects", { method: "POST", body: { name } });
    if (p.join_key) store.joinKeys[p.id] = p.join_key;  // shown once by the server; kept for this tab only
    await loadProjects(); return p;
  }
  async function rotateJoinKey(projectId) {
    const r = await api(`/projects/${projectId}/join-key`, { method: "POST" });
    store.joinKeys[projectId] = r.join_key; return r.join_key;
  }
  async function reconcile(agentId, files, base) {
    const r = await api("/reconcile", { method: "POST", body: { project_id: store.projectId, agent_id: agentId, base: base || null, files } });
    await refresh(); return r;
  }
  async function seedDemo() {
    const p = await api("/demo/seed", { method: "POST" });
    await loadProjects(); return p;
  }

  Object.assign(CT, {
    $, el, dur, clock, word, storage, api, ApiError, on, emit, store, active, agentById, agentName, agentColor, agentState,
    target, refusals, editFindings, rangeOf, lineCount, loadContent, selectProject, loadProjects, refresh, poll,
    claim, release, send, register, createProject, seedDemo, rotateJoinKey, reconcile, ts, auth,
    reduceMotion: () => matchMedia("(prefers-reduced-motion: reduce)").matches,
  });
})();