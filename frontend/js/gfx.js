/*
 * Shared WebGL plumbing for the two 3D scenes: the low-resolution render
 * target, the post pass (soft bloom, violet aurora, ordered dither, grain),
 * a glow sprite texture, and a base Scene class that owns a renderer, a
 * canvas that moves between pages, an orbit camera and pointer handling.
 */
"use strict";
(() => {
  const { el } = CT;
  const hasGL = () => !!window.THREE;

  const POST_FRAG = `
    precision highp float; varying vec2 vUv;
    uniform sampler2D tScene; uniform vec2 uRes; uniform float uTime; uniform float uFlash;
    uniform vec2 uAuroraCenter; uniform float uBloom; uniform float uLevels;
    float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
    float b2(vec2 a){a=floor(a);return fract(dot(a,vec2(.5,a.y*.75)));}
    float b4(vec2 a){return b2(.5*a)*.25+b2(a);}
    float b8(vec2 a){return b4(.5*a)*.25+b2(a);}
    vec3 bright(vec2 uv){ return max(texture2D(tScene, uv).rgb - .35, 0.); }
    void main(){
      vec2 px = 1. / uRes;
      vec3 col = texture2D(tScene, vUv).rgb;
      if (uBloom > 0.) {
        vec3 bl = vec3(0.);
        for (int i = 0; i < 8; i++) { float a = float(i) * .785398; vec2 o = vec2(cos(a), sin(a));
          bl += bright(vUv + o * px * 3.) * .5 + bright(vUv + o * px * 7.) * .35 + bright(vUv + o * px * 13.) * .2; }
        col += bl * .32 * uBloom;
      }
      vec2 p = vUv; float asp = uRes.x / uRes.y;
      vec2 d = vec2((p.x - uAuroraCenter.x) * asp, p.y - uAuroraCenter.y);
      float ang = atan(d.x, d.y), r = length(d);
      float rays = pow(.5+.5*sin(ang*17.+uTime*.2),7.)*.55 + pow(.5+.5*sin(ang*33.-uTime*.15+1.3),11.)*.45 + pow(.5+.5*sin(ang*7.+uTime*.1),3.)*.2;
      float mask = smoothstep(1.2, .25, r) * smoothstep(.06, .32, r);
      float lum = dot(col, vec3(.299, .587, .114));
      col += mix(vec3(.28,.19,.70), vec3(.78,.65,1.), rays) * (rays*.5+.15) * mask * .5 * (1. - smoothstep(0., .28, lum));
      col += vec3(1., .3, 0.) * uFlash * .12;
      col *= 1. - .5 * pow(length((p - vec2(.5, .5)) * vec2(1., 1.15)), 2.);
      col += (h(gl_FragCoord.xy + fract(uTime) * 91.) - .5) * .03;
      float th = b8(gl_FragCoord.xy) - .5;
      col = clamp(floor(col * uLevels + th + .5) / uLevels, 0., 1.);
      gl_FragColor = vec4(col, 1.);
    }`;

  let glowTex = null;
  function glowTexture() {
    if (glowTex) return glowTex;
    const c = document.createElement("canvas"); c.width = c.height = 64;
    const g = c.getContext("2d"), grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grd.addColorStop(0, "rgba(255,255,255,1)"); grd.addColorStop(.25, "rgba(255,255,255,.55)"); grd.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grd; g.fillRect(0, 0, 64, 64);
    return (glowTex = new THREE.CanvasTexture(c));
  }
  function glowSprite(color, size) {
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(), color, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
    s.scale.setScalar(size); return s;
  }
  const invisible = () => new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, colorWrite: false });

  /*
   * Base scene. Subclasses implement build(), sync(), step(t, dt, time),
   * pickables(), hover(hit, e), click(hit, e) and optionally onEvent(ev).
   */
  class Scene {
    constructor({ pixel = 3, bloom = 0, levels = 6, aurora = [0.5, -0.28], view }) {
      this.ok = hasGL();
      this.opts = { pixel, bloom, levels, aurora };
      this.view = { yaw: 0, pitch: 0.35, dist: 14, auto: true, drag: false, moved: 0, mx: 0, my: 0, zoom: 0, ...view };
      this.home = { ...this.view };
      this.canvas = el("canvas", { class: "gl-canvas", "aria-hidden": "true" });
      this.tagLayer = el("div", { class: "tags", "aria-hidden": "true" });
      this.tip = el("div", { class: "tip", role: "status" });
      this.mounted = false; this.flash = 0;
      if (!this.ok) return;
      try { this.R = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: false }); } catch { this.ok = false; return; }
      this.R.setPixelRatio(1);
      this.scene = new THREE.Scene();
      this.cam = new THREE.PerspectiveCamera(40, 1, 0.1, 300);
      this.rt = new THREE.WebGLRenderTarget(4, 4, { minFilter: THREE.LinearFilter, magFilter: THREE.NearestFilter });
      this.post = new THREE.ShaderMaterial({
        uniforms: { tScene: { value: this.rt.texture }, uRes: { value: new THREE.Vector2(1, 1) }, uTime: { value: 0 }, uFlash: { value: 0 },
          uAuroraCenter: { value: new THREE.Vector2(...aurora) }, uBloom: { value: bloom }, uLevels: { value: levels } },
        vertexShader: "varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.,1.); }",
        fragmentShader: POST_FRAG, depthTest: false, depthWrite: false,
      });
      this.postScene = new THREE.Scene(); this.postCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
      this.postScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.post));
      this.ray = new THREE.Raycaster();
      this.bindPointer();
      new ResizeObserver(() => this.resize()).observe(this.canvas);
      this.build();
      this.last = 0;
      const loop = (t) => { requestAnimationFrame(loop); if (this.mounted) this.frame(t); };
      requestAnimationFrame(loop);
    }

    mount(stage) {
      stage.prepend(this.tagLayer); stage.prepend(this.canvas); stage.append(this.tip);
      this.mounted = true; this.stage = stage;
      requestAnimationFrame(() => this.resize());
    }
    unmount() { this.mounted = false; this.canvas.remove(); this.tagLayer.remove(); this.tip.remove(); this.hideTip(); this.closePop?.(); }

    resize() {
      if (!this.ok || !this.mounted) return;
      const r = this.canvas.getBoundingClientRect(); if (!r.width) return;
      const px = r.width < 700 ? 2 : this.opts.pixel;
      const w = Math.max(2, Math.ceil(r.width / px)), h = Math.max(2, Math.ceil(r.height / px));
      this.R.setSize(w, h, false); this.rt.setSize(w, h); this.post.uniforms.uRes.value.set(w, h);
      this.cam.aspect = r.width / r.height; this.cam.updateProjectionMatrix();
      this.narrow = r.width < 760;
    }

    resetView() { Object.assign(this.view, { yaw: this.home.yaw, pitch: this.home.pitch, zoom: 0, auto: true }); }
    zoomBy(d) { this.view.zoom = Math.max(-0.5, Math.min(1, this.view.zoom + d * 0.12)); }

    bindPointer() {
      const v = this.view, c = this.canvas;
      c.addEventListener("pointerdown", (e) => { v.drag = true; v.moved = 0; v.auto = false; v.px = e.clientX; v.py = e.clientY; c.setPointerCapture(e.pointerId); });
      c.addEventListener("pointermove", (e) => {
        const r = c.getBoundingClientRect(); v.mx = (e.clientX - r.left) / r.width - .5; v.my = (e.clientY - r.top) / r.height - .5;
        if (v.drag) {
          const dx = e.clientX - v.px, dy = e.clientY - v.py; v.moved += Math.abs(dx) + Math.abs(dy);
          v.yaw -= dx * .006; v.pitch = Math.max(this.pitchMin ?? .1, Math.min(1.1, v.pitch + dy * .004)); v.px = e.clientX; v.py = e.clientY;
          if (v.moved > 6) { this.hideTip(); return; }
        }
        this.hoverAt(e);
      });
      c.addEventListener("pointerup", (e) => { const click = v.moved <= 6; v.drag = false; if (click) this.clickAt(e); this.resumeAuto(); });
      c.addEventListener("pointerleave", () => { this.hovered = null; v.mx = v.my = 0; this.hideTip(); this.resumeAuto(); });
    }
    resumeAuto() { clearTimeout(this.autoT); this.autoT = setTimeout(() => { if (!this.hovered && !this.popOpen) this.view.auto = true; }, 2500); }

    pick(e) {
      if (!this.ok) return null;
      const r = this.canvas.getBoundingClientRect();
      this.ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.cam);
      const hit = this.ray.intersectObjects(this.pickables(), false)[0];
      return hit ? { hit, x: e.clientX - r.left, y: e.clientY - r.top } : null;
    }
    hoverAt(e) {
      const h = this.pick(e);
      this.canvas.style.cursor = h ? "pointer" : "";
      if (h) this.view.auto = false;
      this.hovered = h;
      if (!h || this.popOpen) return this.hideTip();
      const content = this.hover(h);
      if (!content) return this.hideTip();
      this.tip.replaceChildren(...content.nodes);
      if (content.color) this.tip.style.setProperty("--c", content.color); else this.tip.style.removeProperty("--c");
      this.tip.classList.add("show"); this.place(this.tip, h.x, h.y);
    }
    clickAt(e) { const h = this.pick(e); this.click(h, e); }
    hideTip() { this.tip.classList.remove("show"); }
    place(node, x, y) {
      const box = this.stage.getBoundingClientRect(), w = node.offsetWidth || 260, h = node.offsetHeight || 120;
      let left = x + 16, top = y + 16;
      if (left + w > box.width - 8) left = x - w - 16;
      if (top + h > box.height - 8) top = y - h - 16;
      top = Math.min(top, box.height - h - 8);
      node.style.transform = `translate(${Math.max(8, left)}px, ${Math.max(60, top)}px)`;
    }

    frame(t) {
      const dt = Math.min(64, t - this.last); this.last = t;
      const reduce = CT.reduceMotion(), time = reduce ? 0 : t / 1000;
      if (this.view.auto && !reduce) this.view.yaw += dt * (this.spin ?? .00006);
      this.step(t, dt, time);
      this.flash = Math.max(0, this.flash - dt / 900);
      this.post.uniforms.uTime.value = time; this.post.uniforms.uFlash.value = this.flash;
      this.R.setRenderTarget(this.rt); this.R.render(this.scene, this.cam);
      this.R.setRenderTarget(null); this.R.render(this.postScene, this.postCam);
      this.placeTags?.();
    }

    project(v3) {
      const r = this.canvas.getBoundingClientRect(), v = v3.clone().project(this.cam);
      return { x: (v.x * .5 + .5) * r.width, y: (-v.y * .5 + .5) * r.height, behind: v.z > 1, w: r.width, h: r.height };
    }
  }

  CT.gfx = { Scene, glowSprite, glowTexture, invisible, hasGL };
})();