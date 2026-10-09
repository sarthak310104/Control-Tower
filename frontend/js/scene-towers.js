/*
 * Home hero: the codebase as wireframe towers. One tower per file the
 * coordinator has a snapshot of; one floor per line. Active claims are
 * glowing slabs at the lines they cover, in the holder's colour. Hover reads
 * a floor, a click on a free floor claims it through the real API.
 */
"use strict";
(() => {
  const { el, store, active, agentColor, agentName, target, rangeOf, lineCount } = CT;
  const W = 1.35;

  class Towers extends CT.gfx.Scene {
    constructor() {
      super({ pixel: 3, bloom: 0, levels: 6, aurora: [0.5, -0.28], view: { yaw: -0.55, pitch: 0.38 } });
      this.pop = el("div", { class: "pop", role: "dialog", "aria-label": "Claim code" }); this.pop.hidden = true;
      document.addEventListener("pointerdown", (e) => { if (!this.pop.hidden && !this.pop.contains(e.target) && e.target !== this.canvas) this.closePop(); });
    }
    mount(stage) { super.mount(stage); stage.append(this.pop); this.sync(); }
    unmount() { super.unmount(); this.pop.remove(); }
    get canClaim() { return this.interactive !== false && !!store.projectId; }

    build() {
      this.towers = {}; this.claimMeshes = []; this.beacons = []; this.rings = []; this.sig = "";
      const g = [], ext = 18;
      for (let i = -ext; i <= ext; i++) g.push(i, 0, -ext, i, 0, ext, -ext, 0, i, ext, 0, i);
      this.scene.add(new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(g, 3)), new THREE.LineBasicMaterial({ color: 0x1d1d26 })));
      const hoverMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: .18, depthWrite: false });
      this.hoverFloor = new THREE.Group();
      this.hoverFloor.add(new THREE.Mesh(new THREE.BoxGeometry(W * 1.1, 1, W * 1.1), hoverMat));
      this.hoverFloor.add(new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(W * 1.12, 1, W * 1.12)), new THREE.LineBasicMaterial({ color: 0xffffff })));
      this.hoverFloor.visible = false; this.scene.add(this.hoverFloor);
    }

    /* ---------- layout from store ---------- */
    get files() { return store.files.map((f) => f.file_path); }
    floor() { const max = Math.max(8, ...store.files.map((f) => f.line_count)); return Math.min(0.16, 5.2 / max); }
    sync() {
      if (!this.ok) return;
      const files = this.files, FLOOR = this.floor();
      const fileSig = files.map((f) => `${f}:${lineCount(f)}`).join("|") + FLOOR;
      if (fileSig !== this.fileSig) { this.fileSig = fileSig; this.buildTowers(files, FLOOR); }
      const claimSig = active().map((c) => `${c.id}:${rangeOf(c)}`).join("|") + "|" + store.agents.map((a) => a.id).join();
      if (claimSig !== this.claimSig) { this.claimSig = claimSig; this.buildClaims(FLOOR); }
    }
    buildTowers(files, FLOOR) {
      Object.values(this.towers).forEach((t) => this.scene.remove(t.group)); this.towers = {};
      const cols = Math.max(1, Math.ceil(Math.sqrt(files.length * 1.8))), rows = Math.ceil(files.length / cols), SP = 3.1;
      files.forEach((file, i) => {
        const col = i % cols, row = Math.floor(i / cols);
        const p = new THREE.Vector3((col - (cols - 1) / 2) * SP, 0, (row - (rows - 1) / 2) * SP * 1.1 + (col % 2 ? -.6 : .5));
        const n = lineCount(file), h = n * FLOOR;
        const group = new THREE.Group(); group.position.copy(p);
        const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(W, h, W)), new THREE.LineBasicMaterial({ color: 0x8b8e9c }));
        box.position.y = h / 2; group.add(box);
        const f = [], hw = W / 2, every = Math.max(1, Math.ceil(n / 60));
        for (let k = every; k < n; k += every) { const y = k * FLOOR; f.push(-hw, y, -hw, hw, y, -hw, hw, y, -hw, hw, y, hw, hw, y, hw, -hw, y, hw, -hw, y, hw, -hw, y, -hw); }
        group.add(new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(f, 3)), new THREE.LineBasicMaterial({ color: 0x2c2c38 })));
        const pick = new THREE.Mesh(new THREE.BoxGeometry(W * 1.15, h, W * 1.15), CT.gfx.invisible());
        pick.position.y = h / 2; pick.userData.file = file; group.add(pick);
        this.scene.add(group);
        this.towers[file] = { group, h, p, box, pick, n };
      });
      this.span = Math.max(cols, rows * 1.2) * SP;
      this.tagLayer.replaceChildren();
    }
    buildClaims(FLOOR) {
      this.claimMeshes.forEach((m) => this.scene.remove(m.group)); this.claimMeshes = [];
      this.beacons.forEach((b) => this.scene.remove(b.group)); this.beacons = [];
      for (const c of active()) {
        const tw = this.towers[c.file]; if (!tw) continue;
        const [a, b] = rangeOf(c), y0 = (a - 1) * FLOOR, hgt = Math.max(FLOOR, (b - a + 1) * FLOOR);
        const col = new THREE.Color(agentColor(c.agent));
        const group = new THREE.Group(); group.position.set(tw.p.x, y0 + hgt / 2, tw.p.z);
        const fill = new THREE.Mesh(new THREE.BoxGeometry(W * .96, hgt * .94, W * .96), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: .42, depthWrite: false }));
        const edge = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(W * 1.04, hgt, W * 1.04)), new THREE.LineBasicMaterial({ color: col }));
        group.add(fill, edge); this.scene.add(group);
        this.claimMeshes.push({ id: c.id, c, group, fill, edge, col });
      }
      store.agents.forEach((ag, i) => {
        const mine = active().filter((c) => c.agent === ag.id); const c = mine.at(-1); const tw = c && this.towers[c.file];
        if (!tw) return;
        const col = new THREE.Color(agentColor(ag.id));
        const top = tw.h + 1.1 + (i % 3) * .45, group = new THREE.Group();
        const dot = new THREE.Mesh(new THREE.OctahedronGeometry(.16), new THREE.MeshBasicMaterial({ color: col }));
        dot.position.set(tw.p.x + (i % 2 ? .35 : -.35), top, tw.p.z); dot.scale.setScalar(1.4); dot.userData.agent = ag.id;
        const [, b] = rangeOf(c);
        const beam = new THREE.Line(new THREE.BufferGeometry().setFromPoints([dot.position.clone(), new THREE.Vector3(tw.p.x, b * FLOOR, tw.p.z)]), new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: .5 }));
        group.add(dot, beam); this.scene.add(group);
        this.beacons.push({ agent: ag.id, group, dot });
      });
      this.tagLayer.replaceChildren();
    }

    /* ---------- events from the activity feed ---------- */
    onEvent(e) {
      const file = e.payload?.file_path, tw = file && this.towers[file];
      if (e.type === "claim_rejected") { this.ring(tw, 0xff4d00); this.flashClaim = { id: e.payload.conflicting_claim_id, until: performance.now() + 900 }; this.flash = 1; }
      if (e.type === "claim_expired" || e.type === "claim_invalidated" || e.type === "edit_unclaimed") this.ring(tw, 0xff4d00);
      if (e.type === "edit_conflict") { this.ring(tw, 0xff4d00); this.flashClaim = { id: e.payload.overlaps?.[0]?.claim_id, until: performance.now() + 900 }; this.flash = 1; }
      if (e.type === "claim_created") this.ring(tw, 0xe6e6e6);
    }
    ring(tw, color) {
      if (!tw || !this.ok) return;
      const pts = []; for (let i = 0; i <= 64; i++) { const a = i / 64 * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(a), 0, Math.sin(a))); }
      const ring = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color, transparent: true }));
      ring.position.set(tw.p.x, .02, tw.p.z); this.scene.add(ring); this.rings.push({ ring, t0: performance.now() });
    }

    /* ---------- per frame ---------- */
    step(t, dt, time) {
      this.sync();
      const v = this.view, base = (this.narrow ? 2.1 : 1.35) * Math.max(9, (this.span || 8) + 4);
      const dist = base * (1 + v.zoom);
      const yaw = v.yaw + v.mx * .12, pitch = v.pitch - v.my * .06;
      const lookY = 1.4;
      this.cam.position.set(Math.sin(yaw) * Math.cos(pitch) * dist, Math.sin(pitch) * dist + lookY, Math.cos(yaw) * Math.cos(pitch) * dist);
      this.cam.lookAt(0, lookY, 0);
      const FLOOR = this.floor(), now = Date.now();
      for (const [f, tw] of Object.entries(this.towers)) tw.box.material.color.set(this.hoverInfo?.file === f ? 0xe6e6e6 : 0x8b8e9c);
      if (this.hoverInfo && this.towers[this.hoverInfo.file]) {
        const tw = this.towers[this.hoverInfo.file]; this.hoverFloor.visible = true;
        this.hoverFloor.scale.set(1, FLOOR, 1); this.hoverFloor.position.set(tw.p.x, (this.hoverInfo.line - .5) * FLOOR, tw.p.z);
      } else this.hoverFloor.visible = false;
      const flashing = this.flashClaim && performance.now() < this.flashClaim.until ? this.flashClaim.id : null;
      for (const m of this.claimMeshes) {
        const frac = Math.max(0, (store.ttl - (now - m.c.beat)) / store.ttl), crit = frac < .15;
        if (m.id === flashing) { m.fill.material.color.set(0xff4d00); m.edge.material.color.set(0xff4d00); m.fill.material.opacity = .85; }
        else { const c = crit ? new THREE.Color(0xff4d00) : m.col; m.fill.material.color.copy(c); m.edge.material.color.copy(c); m.fill.material.opacity = .3 + .14 * Math.sin(time * (crit ? 12 : 2) + m.id); }
      }
      for (const b of this.beacons) b.dot.rotation.y = time * 1.5;
      this.rings = this.rings.filter((r) => { const k = (t - r.t0) / 1600; if (k > 1) { this.scene.remove(r.ring); return false; } const s = .8 + k * 6; r.ring.scale.set(s, 1, s); r.ring.material.opacity = 1 - k; return true; });
    }

    placeTags() {
      const want = Object.keys(this.towers).length + this.beacons.length;
      if (this.tagLayer.children.length !== want) {
        this.tagLayer.replaceChildren(...Object.keys(this.towers).map((f) => el("span", { class: "tag file", "data-file": f }, f)),
          ...this.beacons.map((b) => el("span", { class: "tag agent", "--c": agentColor(b.agent), "data-agent": b.agent }, agentName(b.agent))));
      }
      for (const n of this.tagLayer.children) {
        let p;
        if (n.dataset.file) { const tw = this.towers[n.dataset.file]; if (!tw) continue; p = this.project(new THREE.Vector3(tw.p.x - W / 2, -.25, tw.p.z + W / 2)); }
        else { const b = this.beacons.find((x) => x.agent === Number(n.dataset.agent)); if (!b) continue; p = this.project(b.dot.position); p.x += 12; p.y -= 6; }
        n.toggleAttribute("data-off", p.behind || p.x < 0 || p.x > p.w - 40 || p.y < 60 || p.y > p.h);
        n.style.transform = `translate(${Math.round(p.x)}px, ${Math.round(p.y)}px)`;
      }
    }

    /* ---------- interaction ---------- */
    pickables() { return [...Object.values(this.towers).map((t) => t.pick), ...this.beacons.map((b) => b.dot)]; }
    info(h) {
      if (!h) return null;
      const o = h.hit.object;
      if (o.userData.agent) return { agent: o.userData.agent };
      const file = o.userData.file, n = lineCount(file);
      const line = Math.min(n, Math.max(1, Math.floor(h.hit.point.y / this.floor()) + 1));
      return { file, line };
    }
    holderOf(file, line) { return active().find((c) => c.file === file && (([a, b]) => line >= a && line <= b)(rangeOf(c))); }
    symbolAt(file, line) {
      const syms = store.contents[file]?.symbols; if (!syms) return null;
      return syms.filter((s) => line >= s.line_start && line <= s.line_end).sort((a, b) => (a.line_end - a.line_start) - (b.line_end - b.line_start))[0] || null;
    }
    hoverAt(e) { super.hoverAt(e); this.hoverInfo = this.info(this.hovered); if (this.hoverInfo?.agent) this.hoverInfo = null; }
    hover(h) {
      const i = this.info(h);
      if (i.agent) {
        const cs = active().filter((c) => c.agent === i.agent);
        return { color: agentColor(i.agent), nodes: [el("span", { class: "t-title" }, agentName(i.agent)), el("span", { class: "t-sub" }, `${cs.length} ${cs.length === 1 ? "claim" : "claims"}`), el("span", { class: "t-hint" }, "Click to see its claims")] };
      }
      const c = this.holderOf(i.file, i.line), sym = this.symbolAt(i.file, i.line);
      const src = (store.contents[i.file]?.source || "").split("\n")[i.line - 1] || "";
      return {
        color: c ? agentColor(c.agent) : "var(--ok)",
        nodes: [
          el("span", { class: "t-title", style: "color:var(--text)" }, i.file),
          el("span", { class: "t-sub" }, `Line ${i.line} of ${lineCount(i.file)}`),
          el("code", {}, sym ? sym.symbol : (src.trim() ? src.trim().slice(0, 36) : "blank line")),
          el("span", { class: "t-row" }, el("i"), c ? `${agentName(c.agent)} holds ${target(c)}` : "Free to claim"),
          el("span", { class: "t-hint" }, !this.canClaim ? "Open a workspace to claim code" : c ? "Click to open this claim" : "Click to claim it"),
        ],
      };
    }
    click(h) {
      if (!h) return this.closePop();
      if (!this.canClaim) return;
      const i = this.info(h);
      if (i.agent) return CT.ui?.filterAgent(i.agent);
      const c = this.holderOf(i.file, i.line);
      if (c) { this.closePop(); return CT.ui?.openClaim(c.id); }
      this.openPop(i, h);
    }
    closePop() { if (this.pop) this.pop.hidden = true; this.popOpen = false; this.resumeAuto(); }
    openPop(i, h) {
      if (!store.agents.length) return CT.ui?.toast("Register an agent first. Press S to open Simulate.", true);
      this.hideTip(); this.view.auto = false; this.popOpen = true;
      const sym = this.symbolAt(i.file, i.line);
      const sel = el("select", { class: "field-in", id: "pop-agent" }, store.agents.map((a) => el("option", { value: a.id }, a.name)));
      if (this.popAgent && store.agents.some((a) => a.id === this.popAgent)) sel.value = String(this.popAgent);
      const out = el("output", { class: "result", "aria-live": "polite" });
      const run = async (req) => {
        this.popAgent = Number(sel.value);
        out.className = "result"; out.textContent = "Claiming…";
        try {
          const r = await CT.claim({ agentId: this.popAgent, file: i.file, note: "Claimed from the dashboard", ...req });
          if (r.refused) { out.className = "result bad"; out.replaceChildren("Refused. ", el("code", {}, r.refused.symbol_name || `lines ${r.refused.line_start}–${r.refused.line_end}`), ` is held by ${r.refused.conflicting_agent}.`); this.place(this.pop, h.x, h.y); }
          else { this.closePop(); CT.ui?.toast(`${agentName(this.popAgent)} claimed ${target(r.claim)}.`); }
        } catch (err) { out.className = "result bad"; out.textContent = err.message; this.place(this.pop, h.x, h.y); }
      };
      const opts = [];
      if (sym) opts.push(el("button", { type: "button", class: "btn btn-primary", onclick: () => run({ symbol: sym.symbol }) }, el("span", {}, "Claim ", el("code", {}, sym.symbol)), el("span", { style: "opacity:.65" }, `${sym.line_start}–${sym.line_end}`)));
      opts.push(el("button", { type: "button", class: "btn " + (sym ? "btn-ghost" : "btn-primary"), onclick: () => run({ start: i.line, end: i.line }) }, el("span", {}, `Claim line ${i.line} only`), el("span", { class: "key" }, "range")));
      this.pop.replaceChildren(el("h3", {}, el("code", {}, i.file), `, line ${i.line}`), el("label", { for: "pop-agent" }, "Claim as", sel), el("div", { class: "opts" }, opts), out,
        el("button", { type: "button", class: "btn btn-text", style: "justify-self:start", onclick: () => this.closePop() }, "Cancel ", el("span", { class: "key" }, "[Esc]")));
      this.pop.hidden = false; this.place(this.pop, h.x, h.y); sel.focus();
    }
  }

  CT.Towers = Towers;
})();