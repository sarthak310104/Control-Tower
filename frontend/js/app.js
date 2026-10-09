/*
 * Control Tower dashboard: routing, pages, sheets, Simulate and the sample
 * scenario. Data comes from CT.store (polled from the REST API); every
 * write goes through the same endpoints an agent's MCP server uses.
 */
"use strict";
(() => {
  const { $, el, dur, clock, word, store, active, agentById, agentName, agentColor, agentState, target, refusals, rangeOf, lineCount, storage } = CT;
  const ROUTES = ["workspace", "home", "dashboard", "claims", "agents", "activity"];
  let route = "workspace", filter = null, openId = null, lastFocus = null;

  /* ---------------- toast ---------------- */
  let toastT;
  function toast(msg, bad) { const t = $("#toast"); t.textContent = msg; t.classList.toggle("bad", !!bad); t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 3800); }

  /* ---------------- scenes ---------------- */
  const scenes = { towers: new CT.Towers(), orbit: new CT.Orbit() };
  function mountScenes() {
    Object.values(scenes).forEach((s) => s.unmount());
    const slot = document.querySelector(".view:not([hidden]) .gl-slot");
    if (!slot) return;
    const sc = scenes[slot.dataset.scene];
    if (!sc.ok) { slot.parentElement.style.background = "radial-gradient(60% 45% at 55% 70%, rgba(171,143,241,.35), transparent 70%), var(--ground)"; return; }
    sc.interactive = route !== "workspace";
    sc.mount(slot.parentElement);
  }
  CT.on("event", (e) => Object.values(scenes).forEach((s) => s.mounted && s.onEvent?.(e)));

  /* ---------------- routing ---------------- */
  function go() {
    const h = location.hash.replace("#", "");
    route = ROUTES.includes(h) ? h : store.projectId ? "home" : "workspace";
    if (route !== "workspace" && !store.projectId) { location.replace("#workspace"); return; }
    document.querySelectorAll(".view").forEach((v) => (v.hidden = v.dataset.view !== route));
    const app = route !== "workspace";
    $("#nav").hidden = !app; $("#foot").hidden = !app;
    $("#nav").classList.toggle("over", route === "home" || route === "dashboard");
    document.querySelectorAll(".nav-links a").forEach((a) => (a.dataset.route === route ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    closeSheets(true);
    mountScenes();
    scrollTo(0, 0);
    render();
    if (route === "dashboard") requestAnimationFrame(CT.timeline.render);
    if (connectAfterRoute && route === "home") { connectAfterRoute = false; openConnect(); }
  }
  addEventListener("hashchange", go);

  /* ---------------- render ---------------- */
  function render() {
    renderNav();
    if (route === "workspace") renderWorkspace();
    if (route === "home") renderHome();
    if (route === "dashboard") renderDashboard();
    if (route === "claims") renderClaims();
    if (route === "agents") renderAgents();
    if (route === "activity") renderActivity();
    syncSelects();
    if (openId != null && !$("#detail").hidden) openClaim(openId, true, true);
    tick();
  }
  CT.on("change", render);
  CT.on("projects", () => { renderNav(); if (route === "workspace") renderWorkspace(); });
  CT.on("online", (up) => { const o = $("#online"); o.dataset.state = up ? "ok" : "down"; if (!up) o.textContent = "Coordinator unreachable"; });

  function renderNav() {
    const n = active().length;
    $("#n-claims").textContent = store.projectId ? `(${n})` : "";
    $("#n-agents").textContent = store.projectId ? `(${store.agents.length})` : "";
    if (store.online) $("#online").textContent = `${store.agents.filter((a) => agentState(a) === "active").length} online`;
    const sel = $("#proj");
    const sig = store.projects.map((p) => p.id).join() + ":" + store.projectId;
    if (sel.dataset.sig !== sig) {
      sel.replaceChildren(...store.projects.map((p) => el("option", { value: p.id }, p.name)));
      sel.value = String(store.projectId ?? ""); sel.dataset.sig = sig;
    }
    $("#foot-note").textContent = store.isDemo ? "Sample project. Reload it any time from Switch workspace." : `Workspace ${store.project?.name ?? ""}`;
  }
  $("#proj").addEventListener("change", (e) => CT.selectProject(e.target.value));

  /* workspace picker */
  let needToken = false, connectAfterRoute = false;  // a new workspace opens "Connect an agent" once it's shown
  function renderWorkspace() {
    const locked = !!store.health.auth_enabled && (needToken || !CT.auth.token);
    $("#token-form").hidden = !locked;
    $("#ws-body").hidden = locked;
    if (locked) return;
    const list = $("#ws-list");
    list.replaceChildren(...store.projects.map((p) => el("li", {}, el("button", { type: "button", onclick: () => enter(p.id) }, p.name, el("span", {}, p.id === store.projectId ? "Current" : "Open")))));
    list.hidden = !store.projects.length;
    $("#ws-or").hidden = !store.health.demo_enabled;
    $("#ws-demo").hidden = !store.health.demo_enabled;
    $("#ws-demo").textContent = store.projects.some((p) => p.name === store.health.demo_project) ? "Reload the sample project" : "Load the sample project";
  }
  async function enter(id) { await CT.selectProject(id); location.hash = "home"; }
  $("#token-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = $("#token-status");
    CT.auth.token = $("#token-in").value.trim();
    needToken = false; status.textContent = "Checking…";
    try { await CT.loadProjects(); $("#token-in").value = ""; status.textContent = ""; renderWorkspace(); }
    catch (err) { CT.auth.token = null; needToken = true; status.textContent = err.status === 401 ? "That token was rejected." : err.message; }
  });
  CT.on("unauthorized", () => {
    if (needToken) return;
    CT.auth.token = null; needToken = true;
    $("#token-status").textContent = "Your session needs the admin token again.";
    if (route !== "workspace") location.hash = "workspace"; else renderWorkspace();
  });
  $("#ws-new").addEventListener("submit", async (e) => {
    e.preventDefault(); const name = $("#ws-name").value.trim(); if (!name) return;
    try { const p = await CT.createProject(name); $("#ws-name").value = ""; connectAfterRoute = true; await enter(p.id); }
    catch (err) { $("#ws-status").textContent = err.message; }
  });
  $("#ws-demo").addEventListener("click", async () => {
    const b = $("#ws-demo"); b.disabled = true; $("#ws-status").textContent = "Loading the sample project…";
    try { const p = await CT.seedDemo(); $("#ws-status").textContent = ""; await enter(p.id); }
    catch (err) { $("#ws-status").textContent = err.message; }
    finally { b.disabled = false; }
  });

  /* headline + scenario buttons shared by both heroes */
  function scenarioButtons() {
    document.querySelectorAll(".scenario-actions").forEach((box) => {
      const want = store.isDemo ? "demo" : "sim";
      if (box.dataset.mode === want && !playingChanged) return;
      box.dataset.mode = want;
      box.replaceChildren(...(store.isDemo
        ? [el("button", { type: "button", class: "btn btn-primary play", disabled: playing, onclick: playScenario }, playing ? [el("span", { class: "blink" }), "Playing"] : "Play a scenario"),
           el("button", { type: "button", class: "btn btn-ghost", disabled: playing, onclick: reloadDemo }, "Reset sample")]
        : [el("button", { type: "button", class: "btn btn-primary", onclick: () => openSim() }, "Simulate an agent ", el("span", { style: "opacity:.6" }, "[S]"))]));
    });
    playingChanged = false;
  }

  function renderHome() {
    const n = store.agents.length, k = active().length;
    $("#home-h").textContent = !store.loaded ? "Connecting." : !store.files.length ? "No code claimed yet." : `${word(n)} ${n === 1 ? "agent" : "agents"}, ${word(k).toLowerCase()} ${k === 1 ? "claim" : "claims"}, zero overwrites.`;
    scenarioButtons();
    const expiring = active().filter((c) => store.ttl - (Date.now() - c.beat) < store.ttl * .4).length;
    const r = refusals().filter((e) => e.at > Date.now() - 3_600_000).length;
    const files = new Set(active().map((c) => c.file)).size;
    const cell = (href, label, key, big, foot, extra, hot) => el("a", { class: "cell" + (hot ? " hot" : ""), href },
      el("div", { class: "cell-top" }, el("span", {}, label), el("span", { class: "key" }, key)), el("div", { class: "cell-big" }, String(big).padStart(2, "0")), el("div", { class: "cell-foot" }, foot), extra);
    $("#home-cells").replaceChildren(
      cell("#claims", "Claims", "[C]", k, [el("strong", {}, `Across ${files} ${files === 1 ? "file" : "files"}`), expiring ? el("span", { class: "warn" }, `${expiring} expiring soon`) : el("span", {}, k ? "All heartbeats healthy" : "Nothing reserved")]),
      cell("#agents", "Agents", "[G]", n, [el("strong", {}, `${store.agents.filter((a) => agentState(a) === "active").length} active right now`)],
        el("div", { class: "chips" }, store.agents.slice(0, 6).map((a) => el("span", { class: "chip", "--c": agentColor(a.id) }, el("i"), a.name)))),
      cell("#dashboard", "Collisions stopped", "[D]", r, [el("strong", {}, "In the last hour"), el("span", {}, r ? "Refused before any code was written" : "No overlapping claims")], null, r > 0),
      cell("#activity", "Messages", "[A]", store.messages.length, [el("strong", {}, "Between agents"), el("span", {}, store.messages.length ? `Latest from ${agentName(store.messages.at(-1).from_agent_id)}` : "None yet")]),
    );
    feedInto($("#home-feed"), 6); msgsInto($("#home-msgs"), 3);
  }

  function renderDashboard() {
    const k = active().length, r = refusals().filter((e) => e.at > Date.now() - 3_600_000).length;
    const h = $("#dash-h");
    if (!store.loaded) h.textContent = "Connecting.";
    else if (!store.agents.length) h.textContent = "Nothing in orbit yet.";
    else h.replaceChildren(`${word(k)} ${k === 1 ? "claim" : "claims"} in orbit, `, r ? el("span", { class: "hot" }, `${word(r).toLowerCase()} ${r === 1 ? "collision" : "collisions"} stopped.`) : "no collisions.");
    scenarioButtons();
    const expiring = active().filter((c) => store.ttl - (Date.now() - c.beat) < store.ttl * .4).length;
    const stat = (label, v, hot) => el("div", { class: "stat" + (hot ? " hot" : "") }, el("span", {}, label), el("b", {}, String(v).padStart(2, "0")));
    const edits = CT.editFindings().filter((e) => e.at > Date.now() - 3_600_000).length;
    $("#dash-stats").replaceChildren(stat("Active claims", k), stat("Collisions stopped this hour", r, r > 0), stat("Edits outside a claim this hour", edits, edits > 0), stat("Expiring soon", expiring, expiring > 0));
    CT.timeline.render();
  }

  function renderClaims() {
    const list = active();
    $("#claims-count").textContent = `(${list.length})`;
    $("#claim-filter").replaceChildren(el("span", { class: "lbl" }, "Held by"),
      el("button", { type: "button", class: "fbtn", "aria-pressed": String(!filter), onclick: () => { filter = null; render(); } }, "everyone"),
      ...store.agents.map((a) => el("button", { type: "button", class: "fbtn", "--c": agentColor(a.id), "aria-pressed": String(filter === a.id), onclick: () => { filter = filter === a.id ? null : a.id; render(); } }, el("i"), a.name)));
    const shown = list.filter((c) => !filter || c.agent === filter);
    const box = $("#claim-list");
    if (!shown.length) { box.replaceChildren(el("div", { class: "empty" }, el("strong", {}, "Nothing reserved."), filter ? "This agent holds no claims." : "Agents claim a function before editing it. Press S to claim something yourself.")); return; }
    const byFile = {};
    for (const c of shown) (byFile[c.file] ||= []).push(c);
    box.replaceChildren(...Object.keys(byFile).sort().map((file) => {
      const cs = byFile[file].sort((a, b) => rangeOf(a)[0] - rangeOf(b)[0]);
      return el("div", {}, el("div", { class: "fileband" }, file, el("span", {}, `${cs.length} ${cs.length === 1 ? "claim" : "claims"} · ${lineCount(file)} lines`)),
        ...cs.map((c) => { const [a, b] = rangeOf(c);
          return el("button", { type: "button", class: "row", "--c": agentColor(c.agent), onclick: () => openClaim(c.id), "aria-label": `${target(c)} in ${file}, held by ${agentName(c.agent)}` },
            el("span", {}, el("span", { class: "target" }, target(c), c.symbol ? el("small", {}, `lines ${a}–${b}`) : null), c.note ? el("span", { class: "note" }, c.note) : null),
            el("span", { class: "holder" }, el("i"), el("span", {}, agentName(c.agent))),
            el("span", { class: "held" }, el("span", { "data-since": c.created }, dur(Date.now() - c.created)), el("small", { "data-exp": c.id }, "")),
            el("span", { class: "ttl" }, el("span", { "data-bar": c.id }))); }));
    }));
  }

  function renderAgents() {
    $("#agents-count").textContent = `(${store.agents.length})`;
    if (!store.agents.length) { $("#agent-cells").replaceChildren(el("div", { class: "empty" }, el("strong", {}, "No agents have joined."), "Connect one over MCP with CT_PROJECT set to this workspace, or register one here.")); return; }
    $("#agent-cells").replaceChildren(...store.agents.map((a) => {
      const cs = active().filter((c) => c.agent === a.id), st = agentState(a);
      return el("article", { class: "cell agent-cell", "--c": agentColor(a.id) },
        el("div", { style: "display:grid;gap:12px" }, el("div", { class: "agent-name" }, el("i"), a.name),
          el("div", { class: "agent-meta" }, el("span", { "data-state": st }, { active: "Active", quiet: "Quiet", gone: "Gone quiet" }[st]), el("span", {}, a.role || "No role"), el("span", { "data-seen": a.id }, `Seen ${dur(Date.now() - CT.ts(a.last_seen_at))} ago`))),
        el("div", { class: "agent-claims" }, cs.length ? cs.map((c) => el("button", { type: "button", onclick: () => openClaim(c.id) }, el("code", {}, target(c)), el("span", {}, c.file))) : el("span", { style: "font-size:13px;color:var(--faint)" }, "Holds no claims")),
        el("div", { class: "actions" }, el("button", { type: "button", class: "btn btn-ghost", onclick: () => openSim({ msgTo: a.id }) }, `Message ${a.name}`)));
    }));
  }

  function renderActivity() { $("#ev-count").textContent = `${store.events.length} events`; feedInto($("#feed"), 200); msgsInto($("#msgs"), 100); }

  const BAD_EVENTS = ["claim_rejected", "claim_expired", "claim_invalidated", "edit_unclaimed", "edit_conflict"];
  function describe(e) {
    const p = e.payload || {}, code = (t) => el("code", {}, t);
    const tgt = p.symbol_name || (p.line_start ? `lines ${p.line_start}–${p.line_end}` : "a range");
    switch (e.type) {
      case "agent_registered": return [`joined as ${p.role || "an agent"}`];
      case "claim_created": return ["claimed ", code(tgt), " in ", code(p.file_path), p.note ? `. “${p.note}”` : ""];
      case "claim_released": return ["released ", code(tgt), " in ", code(p.file_path), p.note ? `. “${p.note}”` : ""];
      case "claim_rejected": return ["was refused ", code(tgt), " in ", code(p.file_path), `. ${p.conflicting_agent} holds overlapping code.`];
      case "claim_expired": return ["lost ", code(tgt), " in ", code(p.file_path), p.reason === "max_age" ? " after holding it longer than the maximum" : " after its session went quiet"];
      case "claim_invalidated": return ["lost ", code(tgt), " in ", code(p.file_path), " because that symbol no longer exists"];
      case "message_sent": return [p.to_agent_id ? `messaged ${agentName(p.to_agent_id)}: ` : "told everyone: ", p.preview];
      case "edit_unclaimed":
      case "edit_conflict": {
        const where = p.symbols?.length ? p.symbols.map((s, i) => [i ? ", " : "", code(s)]) : [code(`lines ${(p.ranges || []).map((r) => r[0] === r[1] ? r[0] : `${r[0]}–${r[1]}`).join(", ")}`)];
        const inside = (p.overlaps || []).map((o) => `${o.agent}'s ${o.target}`).join(", ");
        return ["changed ", ...where, " in ", code(p.file_path), " without a claim", inside ? `, inside ${inside}` : ""];
      }
    }
    return [e.type];
  }
  function feedInto(list, max) {
    const items = [...store.events].reverse().slice(0, max);
    if (!items.length) { list.replaceChildren(el("li", { class: "empty" }, el("strong", {}, "Nothing yet."), "Claims, releases, refusals and messages show up here as they happen.")); return; }
    list.replaceChildren(...items.map((e) => el("li", { class: "ev" + (BAD_EVENTS.includes(e.type) ? " bad" : "") + (e.isNew ? " fresh" : "") },
      el("time", { datetime: e.created_at }, clock(e.at)), el("div", {}, el("span", { class: "who", "--c": agentColor(e.agent_id) }, agentName(e.agent_id)), " ", el("span", { class: "what" }, ...describe(e))))));
    store.events.forEach((e) => (e.isNew = false));
  }
  function msgsInto(list, max) {
    if (!store.messages.length) { list.replaceChildren(el("li", { class: "empty" }, el("strong", {}, "No messages yet."), "Agents message each other to hand work over.")); return; }
    list.replaceChildren(...[...store.messages].reverse().slice(0, max).map((m) => el("li", { class: "msg" + (m.isNew ? " fresh" : "") },
      el("div", { class: "msg-head" }, el("span", { class: "who", "--c": agentColor(m.from_agent_id) }, agentName(m.from_agent_id)), "to", el("span", { class: "to" }, m.to_agent_id ? agentName(m.to_agent_id) : "everyone"), el("time", { datetime: m.created_at }, clock(m.at))),
      el("p", { class: "msg-body" }, m.body))));
    store.messages.forEach((m) => (m.isNew = false));
  }

  function tick() {
    const now = Date.now();
    document.querySelectorAll("[data-since]").forEach((n) => (n.textContent = dur(now - Number(n.dataset.since))));
    for (const c of active()) {
      const bar = document.querySelector(`[data-bar="${c.id}"]`); if (!bar) continue;
      const left = store.ttl - (now - c.beat), frac = Math.max(0, left / store.ttl);
      bar.style.width = `${(frac * 100).toFixed(1)}%`;
      document.querySelector(`[data-exp="${c.id}"]`).textContent = frac < .4 ? `expires in ${dur(left)}` : "heartbeat ok";
      bar.closest(".row").dataset.level = frac < .15 ? "crit" : frac < .4 ? "warn" : "ok";
    }
    document.querySelectorAll("[data-seen]").forEach((n) => { const a = agentById(Number(n.dataset.seen)); if (a) n.textContent = `Seen ${dur(now - CT.ts(a.last_seen_at))} ago`; });
  }
  setInterval(tick, 1000);
  setInterval(() => route === "dashboard" && CT.timeline.draw(), 5000);

  /* ---------------- sheets ---------------- */
  function showSheet(sel) { closeSheets(true); lastFocus = document.activeElement; $(sel).hidden = false; $("#scrim").hidden = false; $(sel).querySelector("[data-close]").focus(); }
  function closeSheets(silent) {
    const was = !$("#detail").hidden || !$("#sim").hidden || !$("#connect").hidden;
    $("#detail").hidden = true; $("#sim").hidden = true; $("#connect").hidden = true; $("#scrim").hidden = true; openId = null;
    Object.values(scenes).forEach((s) => s.closePop?.());
    if (!silent && was && lastFocus) lastFocus.focus();
  }
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => closeSheets()));
  $("#scrim").addEventListener("click", () => closeSheets());

  async function openClaim(id, any, refresh) {
    const c = store.claims.find((x) => x.id === id);
    if (!c || (!any && c.status !== "active" && !refresh)) { if (refresh) return; toast("That claim isn't active any more.", true); return; }
    openId = id;
    const live = c.status === "active", holder = agentById(c.agent), [a, b] = rangeOf(c);
    $("#detail").style.setProperty("--c", agentColor(c.agent));
    $("#detail-h").textContent = target(c);
    $("#detail-sub").textContent = !live ? `This claim is ${c.status}${c.release_note ? `: ${c.release_note}` : ""}.`
      : c.symbol ? `Claimed by name, so it follows the code when lines above it change. Right now that's lines ${a}–${b}.` : "A line-range claim. It covers these exact line numbers.";
    $("#detail-facts").replaceChildren(
      el("dt", {}, "Held by"), el("dd", { class: "mono" }, holder?.name ?? "unknown"),
      el("dt", {}, "Note"), el("dd", {}, c.note || "No note"),
      el("dt", {}, live ? "Held for" : "Claimed at"), el("dd", {}, live ? dur(Date.now() - c.created) : clock(c.created)),
      el("dt", {}, live ? "Heartbeat" : "Ended"), el("dd", {}, live ? `${dur(Date.now() - c.beat)} ago` : c.released ? clock(c.released) : "unknown"));
    const rel = $("#detail-release"); rel.hidden = !live; rel.textContent = `Release as ${holder?.name ?? "holder"}`;
    if (!refresh) $("#detail-result").textContent = "";
    $("#detail-file").textContent = c.file;
    const content = store.contents[c.file] || await CT.loadContent(c.file);
    const others = active().filter((x) => x.file === c.file), owner = {};
    for (const o of [...others, c]) { const [s, e] = rangeOf(o); for (let i = s; i <= e; i++) if (!owner[i] || o.id === c.id) owner[i] = o; }
    const src = (content?.source || "").replace(/\n$/, "").split("\n");
    $("#detail-code").replaceChildren(...(content ? src.map((line, i) => { const o = owner[i + 1]; return el("div", { class: o ? "hl" : "", "--c": o ? agentColor(o.agent) : null }, el("span", {}, i + 1), el("span", {}, line || " ")); }) : [el("div", {}, el("span", {}, ""), el("span", {}, "No snapshot of this file yet."))]));
    $("#detail-key").replaceChildren(...[...new Set([...others, c].map((o) => o.agent))].map((id2) => el("span", { "--c": agentColor(id2) }, el("i"), agentName(id2))));
    $("#detail-snap").textContent = content ? `Snapshot from the last claim in this file, ${dur(Date.now() - CT.ts(content.updated_at))} ago.` : "";
    if (!refresh) showSheet("#detail");
  }
  $("#detail-release").addEventListener("click", async () => {
    const c = store.claims.find((x) => x.id === openId); if (!c) return;
    try { await CT.release(c.id, c.agent, "Released from the dashboard"); toast(`Released ${target(c)} for ${agentName(c.agent)}.`); closeSheets(); }
    catch (err) { $("#detail-result").className = "result bad"; $("#detail-result").textContent = err.message; }
  });

  /* ---------------- simulate ---------------- */
  function openSim(opts = {}) {
    if (!store.projectId) return;
    showSheet("#sim"); fillFiles();
    if (opts.msgTo) { $("#m-to").value = String(opts.msgTo); const other = store.agents.find((a) => a.id !== opts.msgTo); if (other) $("#m-from").value = String(other.id); $("#m-body").focus(); }
    if (opts.register) $("#a-name").focus();
  }
  $("#open-sim").addEventListener("click", () => openSim());
  $("#add-agent").addEventListener("click", () => openSim({ register: true }));
  $("#connect-agent").addEventListener("click", () => openConnect());

  /* ---------------- connect an agent ---------------- */
  function openConnect() {
    const p = store.project; if (!p) return;
    const authOn = !!store.health.auth_enabled, key = store.joinKeys[p.id];
    const keyVal = key || "<join key>";
    const env = [["CT_URL", location.origin], ["CT_PROJECT", p.name], ...(authOn ? [["CT_JOIN_KEY", keyVal]] : []), ["CT_AGENT", "claude-backend"], ["CT_ROLE", "backend"]];
    $("#cn-cli").textContent = ["claude mcp add control-tower \\", ...env.map(([k, v]) => `  -e ${k}=${v} \\`), "  -- python /path/to/Control-Tower/run_mcp.py"].join("\n");
    $("#cn-json").textContent = JSON.stringify({ mcpServers: { "control-tower": { command: "python", args: ["/path/to/Control-Tower/run_mcp.py"], env: Object.fromEntries(env) } } }, null, 2);
    $("#cn-rotate").hidden = !authOn;
    $("#cn-result").textContent = "";
    $("#cn-key-note").textContent = !authOn
      ? "Auth is off on this coordinator, so agents join without a key. Set CT_ADMIN_TOKEN on the coordinator to require one."
      : key ? "Shown above. Anyone with this key can join this workspace as a new agent, so share it like a password. It isn't shown again after you leave this tab."
      : "The join key is only shown when it's created. Issue a new one to fill it in above. Agents that already joined keep working.";
    showSheet("#connect");
  }
  $("#cn-rotate").addEventListener("click", async () => {
    try { await CT.rotateJoinKey(store.projectId); openConnect(); $("#cn-result").textContent = "New join key issued. The old one no longer works for new agents."; }
    catch (err) { $("#cn-result").className = "result bad"; $("#cn-result").textContent = err.message; }
  });
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    const pre = $("#" + b.dataset.copy);
    try { await navigator.clipboard.writeText(pre.textContent); b.textContent = "Copied"; setTimeout(() => (b.textContent = "Copy"), 1500); }
    catch { const r = document.createRange(); r.selectNodeContents(pre); const s = getSelection(); s.removeAllRanges(); s.addRange(r); b.textContent = "Selected"; }
  }));
  $("#compose").addEventListener("click", () => openSim({ msgTo: store.agents[0]?.id }));

  function syncSelects() {
    const sig = store.agents.map((a) => a.id + a.name).join();
    if (syncSelects.sig === sig) return; syncSelects.sig = sig;
    for (const sel of document.querySelectorAll(".agent-select")) {
      const prev = sel.value, opts = store.agents.map((a) => el("option", { value: a.id }, a.name));
      if (sel.classList.contains("with-all")) opts.unshift(el("option", { value: "" }, "Everyone"));
      if (!opts.length) opts.push(el("option", { value: "" }, "Register an agent first"));
      sel.replaceChildren(...opts); if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
    }
  }
  function fillFiles() {
    const sel = $("#c-file"), prev = sel.value;
    sel.replaceChildren(...store.files.map((f) => el("option", { value: f.file_path }, f.file_path)), el("option", { value: "__new" }, "New file…"));
    sel.value = [...sel.options].some((o) => o.value === prev) ? prev : sel.options[0].value;
    fillSymbols();
  }
  let symTimer;
  async function fillSymbols() {
    const file = $("#c-file").value, isNew = file === "__new";
    $("#c-new").hidden = !isNew;
    let syms = null;
    if (isNew) {
      const src = $("#c-source").value, path = $("#c-path").value;
      if (src.trim() && path.includes(".")) { try { syms = await CT.api("/claims/symbols", { method: "POST", body: { source: src, file_path: path } }); } catch { syms = null; } }
    } else syms = (store.contents[file] || await CT.loadContent(file))?.symbols;
    const opts = (syms || []).map((s) => el("option", { value: s.symbol }, `${s.symbol}  (${s.line_start}–${s.line_end})`));
    opts.push(el("option", { value: "" }, syms?.length ? "A line range instead" : "A line range"));
    $("#c-symbol").replaceChildren(...opts); lineInputs();
  }
  function lineInputs() {
    const byRange = !$("#c-symbol").value;
    for (const id of ["#c-from", "#c-to"]) { $(id).disabled = !byRange; $(id).required = byRange; }
    if (byRange && !$("#c-from").value) { $("#c-from").value = 1; $("#c-to").value = 3; }
  }
  $("#c-file").addEventListener("change", fillSymbols);
  $("#c-source").addEventListener("input", () => { clearTimeout(symTimer); symTimer = setTimeout(fillSymbols, 300); });
  $("#c-path").addEventListener("input", () => { clearTimeout(symTimer); symTimer = setTimeout(fillSymbols, 300); });
  $("#c-symbol").addEventListener("change", lineInputs);
  $("#f-claim").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("#c-result"), dry = e.submitter?.value === "check", isNew = $("#c-file").value === "__new";
    const req = { agentId: Number($("#c-agent").value), file: isNew ? $("#c-path").value.trim() : $("#c-file").value, symbol: $("#c-symbol").value || null,
      start: Number($("#c-from").value), end: Number($("#c-to").value), note: $("#c-note").value.trim(), source: isNew ? $("#c-source").value : undefined };
    out.className = "result";
    if (!req.agentId) { out.className = "result bad"; out.textContent = "Register an agent first."; return; }
    if (isNew && (!req.file || !req.source.trim())) { out.className = "result bad"; out.textContent = "A new file needs a path and its contents."; return; }
    if (!req.symbol && (!(req.start >= 1) || req.end < req.start)) { out.className = "result bad"; out.textContent = "The line range needs From ≤ To, both 1 or more."; return; }
    out.textContent = dry ? "Checking…" : "Claiming…";
    try {
      const r = await CT.claim(req, dry);
      if (dry) {
        const ck = r.check; out.className = ck.ok ? "result" : "result bad";
        out.replaceChildren(...(ck.ok ? [`Free. Lines ${ck.line_start}–${ck.line_end} don't overlap anyone's claim.`]
          : ["Would collide with ", ...ck.conflicts.flatMap((h, i) => [i ? ", " : "", el("code", {}, h.symbol_name || `lines ${h.line_start}–${h.line_end}`), ` held by ${h.agent_name}`]), "."]));
      } else if (r.refused) {
        out.className = "result bad"; out.replaceChildren("Refused. ", el("code", {}, r.refused.symbol_name || `lines ${r.refused.line_start}–${r.refused.line_end}`), ` is held by ${r.refused.conflicting_agent}.`);
      } else {
        out.replaceChildren("Claimed ", el("code", {}, target(r.claim)), "."); $("#c-note").value = "";
        if (isNew) { $("#c-path").value = ""; $("#c-source").value = ""; fillFiles(); }
      }
    } catch (err) { out.className = "result bad"; out.textContent = err.message; }
  });
  $("#f-msg").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = $("#m-body").value.trim(); if (!body) return toast("Write a message first.", true);
    const from = Number($("#m-from").value), to = $("#m-to").value ? Number($("#m-to").value) : null;
    if (!from) return toast("Register an agent first.", true);
    if (to === from) return toast("Pick someone other than the sender.", true);
    try { await CT.send(from, to, body); $("#m-body").value = ""; toast("Message sent."); } catch (err) { toast(err.message, true); }
  });
  $("#f-agent").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#a-name").value.trim(); if (!name) return toast("Give the agent a name.", true);
    try { await CT.register(name, $("#a-role").value.trim()); e.target.reset(); toast(`${name} joined the workspace.`); } catch (err) { toast(err.message, true); }
  });

  /* ---------------- sample scenario (real API calls) ---------------- */
  let playing = false, playingChanged = false;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const agentNamed = (n) => store.agents.find((a) => a.name === n);
  async function playScenario() {
    if (playing) return;
    const cb = agentNamed("claude-backend"), cf = agentNamed("codex-frontend"), gt = agentNamed("gemini-tests");
    if (!cb || !cf || !gt) return toast("The sample agents are missing. Reset the sample project first.", true);
    playing = true; playingChanged = true; render();
    try {
      let r = await CT.claim({ agentId: gt.id, file: "app/service.py", symbol: "UserService", note: "Refactor for test fixtures" });
      toast(r.refused ? "gemini-tests asked for the whole UserService class. Refused: a method inside it is held." : "gemini-tests claimed UserService.", !!r.refused);
      await wait(3200); await CT.send(gt.id, cb.id, "Blocked on UserService. Hand it over when create() is done?");
      await wait(2800);
      const create = active().find((c) => c.agent === cb.id && c.symbol === "UserService.create");
      if (create) { await CT.release(create.id, cb.id, "Validation and duplicate check are in"); toast("claude-backend released UserService.create."); }
      await wait(2400);
      const helper = active().find((c) => c.agent === gt.id && c.symbol === "helper");
      if (helper) await CT.release(helper.id, gt.id, "Tests merged");
      r = await CT.claim({ agentId: gt.id, file: "app/service.py", symbol: "UserService", note: "Refactor for test fixtures" });
      if (!r.refused) toast("gemini-tests now holds the whole UserService class.");
      await wait(2800);
      r = await CT.claim({ agentId: cf.id, file: "app/service.py", symbol: "UserService.delete", note: "Soft-delete for the account page" });
      if (r.refused) toast("codex-frontend tried UserService.delete. Refused: it's inside the class gemini-tests holds.", true);
      await wait(2600); await CT.send(cf.id, null, "Holding off on delete(). I'll pick it up after the fixtures refactor.");
      await wait(2800);
      const pricing = store.contents["app/pricing.py"] || await CT.loadContent("app/pricing.py");
      const total = pricing?.symbols?.find((s) => s.symbol === "total");
      if (total) {
        await CT.reconcile(cf.id, [{ file_path: "app/pricing.py", source: pricing.source, changed: [[total.line_start + 2, total.line_start + 3]], status: "modified" }], "sample");
        toast("codex-frontend's commit changed total(), which claude-backend holds. Reconciliation flagged it.", true);
      }
    } catch (err) { toast(err.message, true); }
    finally { playing = false; playingChanged = true; render(); }
  }
  async function reloadDemo() { if (playing) return; try { const p = await CT.seedDemo(); await CT.selectProject(p.id); toast("Sample project reset."); } catch (err) { toast(err.message, true); } }

  /* ---------------- keyboard + scene controls ---------------- */
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { const pop = Object.values(scenes).find((s) => s.popOpen); if (pop) return pop.closePop(); return closeSheets(); }
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "");
    if (typing || e.metaKey || e.ctrlKey || e.altKey || route === "workspace") return;
    const k = e.key.toLowerCase(), sc = Object.values(scenes).find((s) => s.mounted);
    if (k === "s") { e.preventDefault(); $("#sim").hidden ? openSim() : closeSheets(); }
    else if (k === "h") location.hash = "home";
    else if (k === "d") location.hash = "dashboard";
    else if (k === "c") location.hash = "claims";
    else if (k === "g") location.hash = "agents";
    else if (k === "a") location.hash = "activity";
    else if (sc && k === "0") sc.resetView();
    else if (sc && (k === "+" || k === "=")) sc.zoomBy(-1);
    else if (sc && (k === "-" || k === "_")) sc.zoomBy(1);
  });
  document.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => {
    const sc = Object.values(scenes).find((s) => s.mounted); if (!sc) return;
    const d = Number(b.dataset.zoom); d === 0 ? sc.resetView() : sc.zoomBy(d);
  }));

  /* hooks the scenes and timeline call back into */
  CT.ui = {
    toast,
    openClaim: (id, any) => openClaim(id, any),
    filterAgent: (id) => { filter = id; location.hash = "claims"; if (route === "claims") render(); },
  };

  /* ---------------- boot ---------------- */
  CT.timeline.bind();
  (async () => {
    try { store.health = await CT.api("/health"); store.healthLoaded = true; } catch { CT.emit("online", false); }
    if (store.health.auth_enabled && !CT.auth.token) { route = "workspace"; location.replace("#workspace"); go(); return; }
    try { await CT.loadProjects(); } catch { CT.emit("online", false); }
    const saved = Number(storage.get("ct.project"));
    const id = store.projects.some((p) => p.id === saved) ? saved : null;
    if (id) await CT.selectProject(id); else await CT.poll();
    go();
  })();
})();