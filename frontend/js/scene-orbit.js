/*
 * Dashboard hero: "Orbit". The repo is a wireframe core, each file a node
 * on its surface, each agent a satellite on its own tilted orbit. An active
 * claim is a beam from the satellite to the file with light flowing along
 * it. Collisions, claims and messages from the live activity feed play as
 * shockwaves, blooms and pulses.
 */
"use strict";
(() => {
  const { el, store, active, agentColor, agentName, target, refusals } = CT;
  const CORE_R = 2.3;

  class Orbit extends CT.gfx.Scene {
    constructor() {
      super({ pixel: 2.5, bloom: 1, levels: 8, aurora: [0.56, 0.47], view: { yaw: 0.6, pitch: 0.26 } });
      this.spin = .00005; this.pitchMin = -.6;
    }
    mount(stage) { super.mount(stage); this.sync(); }

    build() {
      this.fileNodes = {}; this.sats = {}; this.beams = []; this.effects = [];
      this.core = new THREE.Group(); this.scene.add(this.core);
      this.core.add(new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(CORE_R, 2)), new THREE.LineBasicMaterial({ color: 0x5a5a70, transparent: true, opacity: .75 })));
      this.core.add(new THREE.Mesh(new THREE.IcosahedronGeometry(CORE_R * .97, 2), new THREE.MeshBasicMaterial({ color: 0x0c0b14 })));
      this.inner = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(CORE_R * .55, 1)), new THREE.LineBasicMaterial({ color: 0xab8ff1, transparent: true, opacity: .55 }));
      this.core.add(this.inner);
      const halo = CT.gfx.glowSprite(0xab8ff1, CORE_R * 2.4); halo.material.opacity = .35; this.core.add(halo);
      const sp = [];
      for (let i = 0; i < 900; i++) { const v = new THREE.Vector3(Math.random() - .5, Math.random() - .5, Math.random() - .5).normalize().multiplyScalar(40 + Math.random() * 30); sp.push(v.x, v.y, v.z); }
      this.scene.add(new THREE.Points(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(sp, 3)), new THREE.PointsMaterial({ color: 0x6a6a80, size: 1.2, sizeAttenuation: false })));
      this.tA = new THREE.Vector3(); this.tB = new THREE.Vector3(); this.tC = new THREE.Vector3();
    }

    /* ---------- layout from store ---------- */
    sync() {
      if (!this.ok) return;
      const fsig = store.files.map((f) => f.file_path).join("|");
      if (fsig !== this.fsig) { this.fsig = fsig; this.buildFiles(); }
      const asig = store.agents.map((a) => a.id).join("|");
      if (asig !== this.asig) { this.asig = asig; this.buildSats(); }
      const bsig = active().map((c) => c.id).join("|");
      if (bsig !== this.bsig) { this.bsig = bsig; this.buildBeams(); }
    }
    buildFiles() {
      Object.values(this.fileNodes).forEach((n) => this.core.remove(n.g)); this.fileNodes = {};
      const files = store.files.map((f) => f.file_path), N = Math.max(1, files.length);
      files.forEach((f, i) => {
        const y = 1 - (i + .5) / N * 2, r = Math.sqrt(1 - y * y), th = i * Math.PI * (3 - Math.sqrt(5));
        const dir = new THREE.Vector3(Math.cos(th) * r, y * .85, Math.sin(th) * r).normalize();
        const g = new THREE.Group(); g.position.copy(dir.multiplyScalar(CORE_R * 1.02));
        const box = new THREE.Mesh(new THREE.BoxGeometry(.16, .16, .16), new THREE.MeshBasicMaterial({ color: 0xe6e6e6 }));
        const halo = CT.gfx.glowSprite(0xe6e6e6, .7);
        const pick = new THREE.Mesh(new THREE.SphereGeometry(.38, 8, 8), CT.gfx.invisible()); pick.userData.file = f;
        g.add(box, halo, pick); this.core.add(g);
        this.fileNodes[f] = { g, box, halo, pick };
      });
      this.bsig = null; this.tagLayer.replaceChildren();
    }
    buildSats() {
      Object.values(this.sats).forEach((s) => { this.scene.remove(s.ring); this.scene.remove(s.group); this.scene.remove(s.trail); }); this.sats = {};
      store.agents.forEach((a, i) => {
        const o = { r: 3.9 + (i % 5) * .85, tx: [.35, -.55, .9, -.2, .6][i % 5], tz: [.15, .4, -.3, .6, -.1][i % 5], speed: [.16, -.12, .1, .14, -.09][i % 5], phase: i * 2.1 };
        const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(o.tx, 0, o.tz)), col = new THREE.Color(agentColor(a.id));
        const pts = []; for (let k = 0; k <= 128; k++) { const t = k / 128 * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(t) * o.r, 0, Math.sin(t) * o.r).applyQuaternion(q)); }
        const ring = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: col, transparent: true, opacity: .35, dashSize: .25, gapSize: .2 }));
        ring.computeLineDistances(); this.scene.add(ring);
        const group = new THREE.Group();
        const body = new THREE.Mesh(new THREE.OctahedronGeometry(.24), new THREE.MeshBasicMaterial({ color: col }));
        const wire = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.OctahedronGeometry(.42)), new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: .6 }));
        const halo = CT.gfx.glowSprite(col, 1.6);
        const pick = new THREE.Mesh(new THREE.SphereGeometry(.6, 8, 8), CT.gfx.invisible()); pick.userData.agent = a.id;
        group.add(body, wire, halo, pick); this.scene.add(group);
        const TN = 40, tg = new THREE.BufferGeometry();
        tg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(TN * 3), 3)); tg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(TN * 3), 3));
        const trail = new THREE.Line(tg, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, blending: THREE.AdditiveBlending }));
        this.scene.add(trail);
        this.sats[a.id] = { a, o, q, col, group, body, wire, halo, pick, ring, trail, hist: [], flash: 0 };
      });
      this.bsig = null; this.tagLayer.replaceChildren();
    }
    satPos(s, time) { const th = s.o.phase + time * s.o.speed; return new THREE.Vector3(Math.cos(th) * s.o.r, 0, Math.sin(th) * s.o.r).applyQuaternion(s.q); }
    buildBeams() {
      this.beams.forEach((b) => { this.scene.remove(b.line); this.scene.remove(b.parts); }); this.beams = [];
      active().forEach((c) => {
        const s = this.sats[c.agent], f = this.fileNodes[c.file]; if (!s || !f) return;
        const N = 48, P = 10;
        const lg = new THREE.BufferGeometry(); lg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(N * 3), 3));
        const pg = new THREE.BufferGeometry(); pg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(P * 3), 3));
        const line = new THREE.Line(lg, new THREE.LineBasicMaterial({ color: s.col, transparent: true, opacity: .55, blending: THREE.AdditiveBlending }));
        const parts = new THREE.Points(pg, new THREE.PointsMaterial({ color: s.col, size: 3, sizeAttenuation: false, transparent: true, blending: THREE.AdditiveBlending }));
        this.scene.add(line, parts);
        this.beams.push({ c, s, f, line, parts, N, P });
      });
    }
    curve(a, b, k, out) {
      const m = this.tC.copy(a).add(b).multiplyScalar(.5); m.setLength(m.length() + 1.6 + a.distanceTo(b) * .15);
      const u = 1 - k; return out.set(u * u * a.x + 2 * u * k * m.x + k * k * b.x, u * u * a.y + 2 * u * k * m.y + k * k * b.y, u * u * a.z + 2 * u * k * m.z + k * k * b.z);
    }

    /* ---------- live events ---------- */
    onEvent(e) {
      if (!this.ok) return;
      const s = this.sats[e.agent_id], f = this.fileNodes[e.payload?.file_path];
      const t0 = performance.now();
      const reaching = e.type === "claim_rejected" || e.type === "edit_conflict";  // an agent went for someone else's code
      if ((reaching || e.type === "claim_expired" || e.type === "edit_unclaimed") && f) {
        if (s && reaching) s.flash = 1;
        this.flash = 1;
        const shock = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(.3, 1)), new THREE.LineBasicMaterial({ color: 0xff4d00, transparent: true, blending: THREE.AdditiveBlending }));
        const SP = 60, sv = []; for (let i = 0; i < SP; i++) sv.push(new THREE.Vector3(Math.random() - .5, Math.random() - .5, Math.random() - .5).normalize().multiplyScalar(1.5 + Math.random() * 3));
        const sg = new THREE.BufferGeometry(); sg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(SP * 3), 3));
        const sparks = new THREE.Points(sg, new THREE.PointsMaterial({ color: 0xff7a33, size: 3, sizeAttenuation: false, transparent: true, blending: THREE.AdditiveBlending }));
        const bg = new THREE.BufferGeometry(); bg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(24 * 3), 3));
        const broken = new THREE.Line(bg, new THREE.LineBasicMaterial({ color: 0xff4d00, transparent: true, blending: THREE.AdditiveBlending }));
        this.scene.add(shock, sparks, broken);
        this.effects.push({ kind: "hit", t0, dur: 1800, s: reaching ? s : null, f, shock, sparks, sv, broken });
      } else if (e.type === "claim_created" && f) {
        const ring = CT.gfx.glowSprite(s ? s.col : 0xffffff, .4); this.scene.add(ring);
        this.effects.push({ kind: "bloom", t0, dur: 1000, f, ring });
      } else if (e.type === "message_sent" && s) {
        const to = e.payload?.to_agent_id ? [this.sats[e.payload.to_agent_id]] : Object.values(this.sats).filter((x) => x !== s);
        to.forEach((tgt) => { if (!tgt) return; const p = CT.gfx.glowSprite(0xffffff, .9); this.scene.add(p); this.effects.push({ kind: "msg", t0, dur: 1400, s, tgt, p }); });
      }
    }

    /* ---------- per frame ---------- */
    step(t, dt, time) {
      this.sync();
      const v = this.view, dist = (this.narrow ? 23 : 16) * (1 + v.zoom);
      const yaw = v.yaw + v.mx * .25, pitch = v.pitch - v.my * .15;
      // Aim above the core so the system sits in the middle of the hero, and a
      // little right on wide screens so the headline on the left can breathe.
      const look = new THREE.Vector3(this.narrow ? 0 : 1.3, this.narrow ? -1.6 : 0.9, 0);
      this.cam.position.set(look.x + Math.sin(yaw) * Math.cos(pitch) * dist, look.y + Math.sin(pitch) * dist, Math.cos(yaw) * Math.cos(pitch) * dist);
      this.cam.lookAt(look);
      this.core.rotation.y = time * .06; this.inner.rotation.set(time * .2, -time * .3, 0);

      const claimed = new Set(active().map((c) => c.file));
      for (const [f, n] of Object.entries(this.fileNodes)) {
        const on = claimed.has(f);
        n.box.material.color.set(on ? 0xffffff : 0x5a5a70); n.halo.material.opacity = on ? .7 + .2 * Math.sin(time * 3 + f.length) : .12;
        n.box.rotation.set(time, time * .7, 0);
      }
      for (const s of Object.values(this.sats)) {
        const p = this.satPos(s, time); s.group.position.copy(p);
        s.body.rotation.set(time * 1.5, time * 2, 0); s.wire.rotation.set(-time, time * .5, 0);
        s.flash = Math.max(0, s.flash - dt / 900);
        const c = s.flash > 0 ? new THREE.Color(0xff4d00).lerp(s.col, 1 - s.flash) : s.col;
        s.body.material.color.copy(c); s.halo.material.color.copy(c); s.halo.scale.setScalar(1.6 + s.flash * 2.5);
        s.hist.unshift(p.clone()); if (s.hist.length > 40) s.hist.pop();
        const pa = s.trail.geometry.attributes.position, ca = s.trail.geometry.attributes.color;
        for (let j = 0; j < 40; j++) { const h = s.hist[Math.min(j, s.hist.length - 1)]; pa.setXYZ(j, h.x, h.y, h.z); const k = 1 - j / 40; ca.setXYZ(j, c.r * k, c.g * k, c.b * k); }
        pa.needsUpdate = ca.needsUpdate = true;
      }
      const now = Date.now();
      for (const b of this.beams) {
        const a = b.s.group.position, fp = b.f.g.getWorldPosition(this.tA).clone();
        const la = b.line.geometry.attributes.position;
        for (let j = 0; j < b.N; j++) { this.curve(a, fp, j / (b.N - 1), this.tB); la.setXYZ(j, this.tB.x, this.tB.y, this.tB.z); } la.needsUpdate = true;
        const pa = b.parts.geometry.attributes.position;
        for (let j = 0; j < b.P; j++) { this.curve(a, fp, (time * .45 + j / b.P) % 1, this.tB); pa.setXYZ(j, this.tB.x, this.tB.y, this.tB.z); } pa.needsUpdate = true;
        const crit = store.ttl - (now - b.c.beat) < store.ttl * .15;
        b.line.material.color.set(crit ? 0xff4d00 : b.s.col); b.parts.material.color.set(crit ? 0xff4d00 : b.s.col);
        b.line.material.opacity = crit ? .35 + .35 * Math.sin(time * 8) : .55;
      }
      this.stepFx(t, time);
      this.flash = Math.max(this.flash, ...Object.values(this.sats).map((s) => s.flash), 0);
    }
    stepFx(t, time) {
      for (let i = this.effects.length - 1; i >= 0; i--) {
        const e = this.effects[i], k = (t - e.t0) / e.dur, done = k >= 1;
        if (e.kind === "hit") {
          const fp = e.f.g.getWorldPosition(this.tA);
          e.shock.position.copy(fp); e.shock.scale.setScalar(1 + k * 9); e.shock.material.opacity = 1 - k;
          const pa = e.sparks.geometry.attributes.position; e.sv.forEach((v, j) => pa.setXYZ(j, fp.x + v.x * k, fp.y + v.y * k - k * k * 1.2, fp.z + v.z * k)); pa.needsUpdate = true; e.sparks.material.opacity = 1 - k;
          if (e.s) { const sp = this.satPos(e.s, time), ba = e.broken.geometry.attributes.position, reach = Math.min(1, k * 3) * .55;
            for (let j = 0; j < 24; j++) { this.curve(sp, fp, (j / 23) * reach, this.tB); ba.setXYZ(j, this.tB.x, this.tB.y, this.tB.z); } ba.needsUpdate = true;
            e.broken.material.opacity = k < .35 ? 1 : 1 - (k - .35) / .65; } else e.broken.visible = false;
          if (done) [e.shock, e.sparks, e.broken].forEach((o) => this.scene.remove(o));
        } else if (e.kind === "bloom") {
          e.ring.position.copy(e.f.g.getWorldPosition(this.tA)); e.ring.scale.setScalar(.4 + k * 3.5); e.ring.material.opacity = 1 - k;
          if (done) this.scene.remove(e.ring);
        } else if (e.kind === "msg") {
          const a = this.satPos(e.s, time), b = this.satPos(e.tgt, time), m = this.tC.copy(a).add(b).multiplyScalar(.5); m.setLength(m.length() + 2.5);
          const u = 1 - k; e.p.position.set(u * u * a.x + 2 * u * k * m.x + k * k * b.x, u * u * a.y + 2 * u * k * m.y + k * k * b.y, u * u * a.z + 2 * u * k * m.z + k * k * b.z);
          e.p.material.opacity = Math.sin(k * Math.PI);
          if (done) this.scene.remove(e.p);
        }
        if (done) this.effects.splice(i, 1);
      }
    }

    placeTags() {
      const want = Object.keys(this.fileNodes).length + Object.keys(this.sats).length;
      if (this.tagLayer.children.length !== want) {
        this.tagLayer.replaceChildren(...Object.keys(this.fileNodes).map((f) => el("span", { class: "tag orbit-file", "data-file": f }, f)),
          ...Object.values(this.sats).map((s) => el("span", { class: "tag agent", "--c": agentColor(s.a.id), "data-agent": s.a.id }, s.a.name, el("small", {}, ""))));
      }
      for (const n of this.tagLayer.children) {
        let world, back = false;
        if (n.dataset.file) {
          const fn = this.fileNodes[n.dataset.file]; if (!fn) continue;
          world = fn.g.getWorldPosition(new THREE.Vector3());
          back = world.clone().normalize().dot(this.cam.position.clone().sub(world).normalize()) < 0;
        } else {
          const s = this.sats[Number(n.dataset.agent)]; if (!s) continue; world = s.group.position;
          n.querySelector("small").textContent = `${active().filter((c) => c.agent === s.a.id).length} held`;
        }
        const p = this.project(world);
        n.toggleAttribute("data-off", p.behind || p.x < 4 || p.x > p.w - 60 || p.y < 56 || p.y > p.h - 10);
        n.classList.toggle("back", back);
        n.style.transform = `translate(${Math.round(p.x + 10)}px, ${Math.round(p.y - 6)}px)`;
      }
    }

    /* ---------- interaction ---------- */
    pickables() { return [...Object.values(this.sats).map((s) => s.pick), ...Object.values(this.fileNodes).map((n) => n.pick)]; }
    hover(h) {
      const d = h.hit.object.userData;
      if (d.agent) {
        const cs = active().filter((c) => c.agent === d.agent), a = CT.agentById(d.agent);
        return { color: agentColor(d.agent), nodes: [el("span", { class: "t-title" }, a?.name), el("span", { class: "t-sub" }, `${a?.role || "agent"} · ${cs.length} ${cs.length === 1 ? "claim" : "claims"}`),
          ...cs.map((c) => el("span", { class: "t-row" }, el("i"), el("code", {}, target(c)), ` in ${c.file}`)), el("span", { class: "t-hint" }, "Click to see its claims")] };
      }
      const cs = active().filter((c) => c.file === d.file), refs = refusals().filter((r) => r.payload.file_path === d.file && r.at > Date.now() - 3_600_000);
      return { nodes: [el("span", { class: "t-title" }, d.file), el("span", { class: "t-sub" }, cs.length ? `${cs.length} active ${cs.length === 1 ? "claim" : "claims"}` : "Nobody is working here"),
        ...cs.map((c) => el("span", { class: "t-row", "--c": agentColor(c.agent) }, el("i"), el("code", {}, target(c)), ` · ${agentName(c.agent)}`)),
        refs.length ? el("span", { class: "t-bad" }, `${refs.length} ${refs.length === 1 ? "collision" : "collisions"} stopped here this hour`) : null,
        el("span", { class: "t-hint" }, cs.length ? "Click to open the first claim" : "Click to open in Claims")] };
    }
    click(h) {
      if (!h) return;
      const d = h.hit.object.userData;
      if (d.agent) return CT.ui?.filterAgent(d.agent);
      const c = active().find((x) => x.file === d.file);
      if (c) CT.ui?.openClaim(c.id); else location.hash = "claims";
    }
  }

  CT.Orbit = Orbit;
})();