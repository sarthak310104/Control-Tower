/*
 * Dashboard timeline: one lane per agent over the last 10/30/60 minutes.
 * Claims are dithered bars from creation to release/expiry (or now),
 * refusals are orange diamonds, messages are dots. Hover scrubs: the
 * readout shows what every agent held at that instant.
 */
"use strict";
(() => {
  const { $, el, store, agentColor, agentName, target, refusals, clock, dur } = CT;
  const LANE = 64, PX = 3;
  const B8 = [0,32,8,40,2,34,10,42,48,16,56,24,50,18,58,26,12,44,4,36,14,46,6,38,60,28,52,20,62,30,54,22,3,35,11,43,1,33,9,41,51,19,59,27,49,17,57,25,15,47,7,39,13,45,5,37,63,31,55,23,61,29,53,21];
  const rgb = (hex) => { const n = parseInt(hex.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
  let win = 30, hoverX = null;

  const endOf = (c) => c.released ?? (c.status === "active" ? null : c.beat);
  function stateAt(t) {
    return store.agents.map((a) => ({
      a, held: store.claims.filter((c) => c.agent === a.id && c.created <= t && (endOf(c) ?? Infinity) >= t),
      refused: refusals().filter((r) => r.agent_id === a.id && Math.abs(r.at - t) < win * 1000),
    }));
  }

  function render() {
    const wrap = $("#tl-wrap"); if (!wrap || wrap.offsetParent === null) return;
    $("#tl-lanes").replaceChildren(...(store.agents.length ? store.agents.map((a) => el("div", { class: "lane-name", "--c": agentColor(a.id), title: a.name }, a.name,
      el("span", {}, `${store.claims.filter((c) => c.agent === a.id && c.status === "active").length} active`))) : [el("div", { class: "lane-name" }, el("span", {}, "No agents yet"))]));
    $("#tl-axis").replaceChildren(...[4, 3, 2, 1, 0].map((k) => el("span", {}, k === 0 ? "now" : `${Math.round(win * k / 4)} min ago`)));
    draw();
  }

  function draw() {
    const wrap = $("#tl-wrap"), cv = $("#tl"); const cssW = wrap.clientWidth; if (!cssW) return;
    const lanes = Math.max(1, store.agents.length), H = LANE * lanes, w = Math.ceil(cssW / PX), h = Math.ceil(H / PX);
    cv.width = w; cv.height = h; cv.style.height = H + "px";
    const ctx = cv.getContext("2d"), img = ctx.createImageData(w, h), d = img.data;
    const now = Date.now(), t0 = now - win * 60_000, xOf = (t) => Math.round(((t - t0) / (now - t0)) * (w - 1));
    const put = (x, y, c) => { if (x < 0 || y < 0 || x >= w || y >= h) return; const i = (y * w + x) * 4; d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 255; };
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) put(x, y, [5, 5, 6]);
    for (let m = 0; m <= win; m += win <= 10 ? 1 : 5) { const x = xOf(t0 + m * 60_000); for (let y = 0; y < h; y += 2) put(x, y, [28, 28, 34]); }
    for (let li = 1; li <= lanes; li++) { const y = Math.round((li * LANE) / PX) - 1; for (let x = 0; x < w; x++) put(x, y, [35, 35, 41]); }
    const laneH = LANE / PX;
    store.claims.forEach((c) => {
      const li = store.agents.findIndex((a) => a.id === c.agent); if (li < 0) return;
      const end = endOf(c) ?? now, x0 = Math.max(0, xOf(c.created)), x1 = Math.min(w - 1, xOf(end)); if (x1 < 0 || x0 > w - 1) return;
      const stack = store.claims.filter((o) => o.agent === c.agent && o.created < end && (endOf(o) ?? now) > c.created && o.id < c.id).length % 3;
      const top = Math.round(li * laneH + 4 + stack * 5), bh = Math.max(3, Math.round(laneH * .42) - stack * 2);
      const col = rgb(agentColor(c.agent)), live = c.status === "active", span = Math.max(1, x1 - x0);
      const bad = c.status === "expired" || c.status === "invalidated";
      for (let x = x0; x <= x1; x++) {
        const k = (x - x0) / span, dens = live ? .25 + .75 * k : .22 + .2 * k;
        for (let y = top; y < top + bh; y++) {
          const edge = y === top || y === top + bh - 1 || x === x0 || (live && x === x1);
          if (edge || B8[(y % 8) * 8 + (x % 8)] / 64 < dens) put(x, y, bad && x === x1 ? [255, 77, 0] : edge && live ? col : col.map((v) => Math.round(v * (live ? 1 : .55))));
        }
      }
    });
    refusals().forEach((r) => {
      const li = store.agents.findIndex((a) => a.id === r.agent_id), x = xOf(r.at); if (li < 0 || x < 0) return;
      const cy = Math.round(li * laneH + laneH * .78);
      for (let dy = -3; dy <= 3; dy++) for (let dx = -3 + Math.abs(dy); dx <= 3 - Math.abs(dy); dx++) put(x + dx, cy + dy, [255, 77, 0]);
    });
    store.messages.forEach((m) => {
      const li = store.agents.findIndex((a) => a.id === m.from_agent_id), x = xOf(m.at); if (li < 0 || x < 0) return;
      const cy = Math.round(li * laneH + laneH * .78);
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) put(x + dx, cy + dy, [230, 230, 230]);
    });
    for (let y = 0; y < h; y++) put(w - 1, y, [171, 143, 241]);
    ctx.putImageData(img, 0, 0);
    if (hoverX != null) readout(hoverX);
  }

  const tAt = (x) => Date.now() - win * 60_000 + (x / $("#tl-wrap").clientWidth) * win * 60_000;
  function readout(x) {
    const wrap = $("#tl-wrap"), ro = $("#tl-readout"), t = tAt(x);
    $("#tl-scrub").style.left = x + "px"; $("#tl-scrub").classList.add("show");
    ro.replaceChildren(el("div", { class: "r-time" }, Date.now() - t < 15_000 ? "Now" : `${clock(t)} · ${dur(Date.now() - t)} ago`),
      ...stateAt(t).map(({ a, held, refused }) => el("div", { class: "r-row", "--c": agentColor(a.id) }, el("i"),
        el("div", {}, el("span", { style: "font:500 12px var(--mono);color:var(--c)" }, a.name, " "),
          held.length ? held.map((c, i) => [i ? ", " : "", el("code", {}, target(c))]) : el("span", {}, "holding nothing"),
          refused.map((r) => el("div", { class: "r-bad" }, `refused ${r.payload.symbol_name || "a range"}, held by ${r.payload.conflicting_agent}`))))));
    ro.classList.add("show");
    const rw = ro.offsetWidth; ro.style.left = (x + 14 + rw > wrap.clientWidth - 8 ? Math.max(8, x - rw - 14) : x + 14) + "px";
  }

  function bind() {
    const wrap = $("#tl-wrap");
    wrap.addEventListener("pointermove", (e) => { const r = wrap.getBoundingClientRect(); hoverX = Math.max(0, Math.min(r.width, e.clientX - r.left)); readout(hoverX); });
    wrap.addEventListener("pointerleave", () => { hoverX = null; $("#tl-readout").classList.remove("show"); $("#tl-scrub").classList.remove("show"); });
    wrap.addEventListener("click", (e) => {
      const r = wrap.getBoundingClientRect(), a = store.agents[Math.floor((e.clientY - r.top) / LANE)], t = tAt(e.clientX - r.left); if (!a) return;
      const c = store.claims.filter((c) => c.agent === a.id && c.created <= t && (endOf(c) ?? Infinity) >= t).at(-1);
      if (c) CT.ui?.openClaim(c.id, true);
    });
    document.querySelectorAll("[data-win]").forEach((b) => b.addEventListener("click", () => {
      win = Number(b.dataset.win); document.querySelectorAll("[data-win]").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); render();
    }));
    new ResizeObserver(draw).observe(wrap);
  }

  CT.timeline = { render, draw, bind };
})();