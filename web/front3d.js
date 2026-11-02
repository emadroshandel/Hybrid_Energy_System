/* 3-D view of the Pareto front.
 *
 * A study usually trades off three objectives (cost, reliability, renewable
 * share) but a flat chart can only show two of them. This view puts all
 * three on axes and colours each design by net present cost.
 *
 * It is loaded only when the user opens it, from a vendored, tree-shaken
 * three.js build (vendor/three/three-lite.js, r187, MIT), so the page still
 * starts instantly and still works with no internet connection.
 *
 * Performance rules followed (three.js best practices):
 *   - one InstancedMesh for every design: one draw call however large the front
 *   - render on demand only (camera move, resize, selection); no idle loop
 *   - pixel ratio capped at 2
 *   - everything rebuilt is disposed first: geometries, materials, textures
 *   - raycasting against the instanced mesh only
 */

import * as THREE from './vendor/three/three-lite.js';

const SIZE = 10;                 // the data cube spans -5..5 on every axis
const H = SIZE / 2;

// Viridis: perceptually uniform and readable with every common colour-vision
// deficiency. The same ramp is drawn in the legend (CSS .colour-ramp).
const VIRIDIS = ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'];
function ramp(t) {
  t = Math.min(1, Math.max(0, t));
  const n = VIRIDIS.length - 1, i = Math.min(n - 1, Math.floor(t * n)), f = t * n - i;
  const a = new THREE.Color(VIRIDIS[i]), b = new THREE.Color(VIRIDIS[i + 1]);
  return a.lerp(b, f);
}

const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';

const view = {
  host: null, renderer: null, scene: null, camera: null, controls: null,
  mesh: null, marker: null, disposables: [], opts: null, ro: null,
  pos: [], raycaster: new THREE.Raycaster(), ndc: new THREE.Vector2(),
  tip: null, frame: 0,
};

function requestRender() {
  if (view.frame) return;
  view.frame = requestAnimationFrame(() => {
    view.frame = 0;
    if (view.renderer) view.renderer.render(view.scene, view.camera);
  });
}

function track(obj) { view.disposables.push(obj); return obj; }

function clearScene() {
  view.disposables.forEach(o => o.dispose && o.dispose());
  view.disposables = [];
  if (view.scene) while (view.scene.children.length) view.scene.remove(view.scene.children[0]);
  view.mesh = null; view.marker = null;
}

/* Text as a camera-facing sprite drawn on a canvas. */
function label(text, color, scale = 0.55) {
  const pad = 12, fs = 44;
  const c = document.createElement('canvas');
  const g = c.getContext('2d');
  g.font = `600 ${fs}px system-ui, Segoe UI, sans-serif`;
  const w = Math.ceil(g.measureText(text).width) + pad * 2;
  c.width = w; c.height = fs + pad * 2;
  g.font = `600 ${fs}px system-ui, Segoe UI, sans-serif`;
  g.fillStyle = color; g.textBaseline = 'middle';
  g.fillText(text, pad, c.height / 2);
  const tex = track(new THREE.CanvasTexture(c));
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = track(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  const s = new THREE.Sprite(mat);
  s.scale.set(scale * c.width / c.height, scale, 1);
  s.renderOrder = 10;
  return s;
}

const short = v => {
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (a >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (a >= 1e4) return (v / 1e3).toFixed(0) + 'k';
  if (a >= 1e3) return (v / 1e3).toFixed(1) + 'k';
  if (a >= 10) return v.toFixed(0);
  return v.toFixed(a >= 1 ? 1 : 3);
};

function ensureRenderer(host) {
  if (view.renderer && view.host === host) return;
  host.innerHTML = '';
  view.host = host;
  const r = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  r.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(r.domElement);
  r.domElement.setAttribute('aria-hidden', 'true');
  view.renderer = r;
  view.scene = new THREE.Scene();
  view.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 200);
  view.camera.position.set(16, 11, 19);
  view.camera.lookAt(0, -0.6, 0);
  view.controls = new THREE.OrbitControls(view.camera, r.domElement);
  view.controls.target.set(0, -0.6, 0);
  view.controls.update();
  view.controls.enableDamping = false;           // damping needs a running loop
  view.controls.minDistance = 8; view.controls.maxDistance = 45;
  view.controls.addEventListener('change', requestRender);

  view.tip = document.createElement('div');
  view.tip.className = 'chart-tip three-tip';
  view.tip.hidden = true;
  view.tip.setAttribute('aria-hidden', 'true');
  host.appendChild(view.tip);

  // Keyboard: arrows orbit, + and - zoom. The table below remains the
  // complete keyboard route to every design; this keeps the view usable.
  host.tabIndex = 0;
  host.setAttribute('aria-label', '3-D Pareto front. Arrow keys rotate, plus and minus zoom.');
  host.addEventListener('keydown', e => {
    const c = view.controls, cam = view.camera, t = c.target;
    const off = cam.position.clone().sub(t);
    const sph = { r: off.length(), th: Math.atan2(off.x, off.z), ph: Math.acos(off.y / off.length()) };
    const k = { ArrowLeft: [-0.12, 0], ArrowRight: [0.12, 0], ArrowUp: [0, -0.1], ArrowDown: [0, 0.1] }[e.key];
    if (k) { sph.th += k[0]; sph.ph = Math.min(Math.PI - 0.15, Math.max(0.15, sph.ph + k[1])); }
    else if (e.key === '+' || e.key === '=') sph.r = Math.max(c.minDistance, sph.r * 0.9);
    else if (e.key === '-') sph.r = Math.min(c.maxDistance, sph.r * 1.1);
    else return;
    e.preventDefault();
    cam.position.set(t.x + sph.r * Math.sin(sph.ph) * Math.sin(sph.th),
                     t.y + sph.r * Math.cos(sph.ph),
                     t.z + sph.r * Math.sin(sph.ph) * Math.cos(sph.th));
    cam.lookAt(t);
    requestRender();
  });

  // Click to open a design; a drag that rotates the view is not a click.
  let down = null;
  r.domElement.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
  r.domElement.addEventListener('pointerup', e => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
    const i = pick(e);
    if (i != null && view.opts && view.opts.onPick) view.opts.onPick(view.opts.points[i].index);
  });
  r.domElement.addEventListener('pointermove', e => {
    if (e.buttons) { view.tip.hidden = true; return; }
    const i = pick(e);
    r.domElement.style.cursor = i != null ? 'pointer' : 'grab';
    if (i == null) { view.tip.hidden = true; return; }
    const box = host.getBoundingClientRect();
    view.tip.textContent = view.opts.points[i].label;
    view.tip.style.whiteSpace = 'normal'; view.tip.style.maxWidth = '260px';
    view.tip.hidden = false;
    const x = e.clientX - box.left, y = e.clientY - box.top;
    view.tip.style.left = Math.min(box.width - view.tip.offsetWidth - 6, x + 14) + 'px';
    view.tip.style.top = Math.max(4, y - view.tip.offsetHeight - 10) + 'px';
  });
  r.domElement.addEventListener('pointerleave', () => { view.tip.hidden = true; });

  view.ro = new ResizeObserver(resize);
  view.ro.observe(host);
}

function resize() {
  const w = view.host.clientWidth, h = view.host.clientHeight;
  if (!w || !h) return;
  view.renderer.setSize(w, h, false);
  view.camera.aspect = w / h;
  view.camera.updateProjectionMatrix();
  requestRender();
}

function pick(e) {
  if (!view.mesh) return null;
  const rect = view.renderer.domElement.getBoundingClientRect();
  view.ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1,
               -((e.clientY - rect.top) / rect.height) * 2 + 1);
  view.raycaster.setFromCamera(view.ndc, view.camera);
  const hit = view.raycaster.intersectObject(view.mesh, false)[0];
  return hit ? hit.instanceId : null;
}

function build(o) {
  clearScene();
  const S = view.scene;
  S.background = new THREE.Color(css('--panel-2'));
  const ink = css('--ink'), muted = css('--muted'), line = css('--line');

  S.add(new THREE.AmbientLight(0xffffff, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 2.2);
  sun.position.set(8, 14, 10);
  S.add(sun);

  const pts = o.points;
  const ext = k => {
    let lo = Infinity, hi = -Infinity;
    pts.forEach(p => { if (isFinite(p[k])) { lo = Math.min(lo, p[k]); hi = Math.max(hi, p[k]); } });
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (hi - lo < 1e-12) {           // every design equal on this axis
      const d = Math.abs(hi) * 0.05 || 1;
      lo = lo >= 0 ? Math.max(0, lo - d) : lo - d; hi += d;
    }
    return [lo, hi];
  };
  const ex = { x: ext('x'), y: ext('y'), z: ext('z'), c: ext('c') };
  const n = (v, [lo, hi]) => (isFinite(v) ? (v - lo) / (hi - lo) : 0) * SIZE - H;

  // Frame and floor grid
  const box = track(new THREE.EdgesGeometry(track(new THREE.BoxGeometry(SIZE, SIZE, SIZE))));
  const lineMat = track(new THREE.LineBasicMaterial({ color: new THREE.Color(line) }));
  S.add(new THREE.LineSegments(box, lineMat));
  const grid = [];
  for (let i = 1; i < 5; i++) {
    const t = -H + i * SIZE / 5;
    grid.push(t, -H, -H, t, -H, H,   -H, -H, t, H, -H, t,   -H, t, -H, -H, t, H,   -H, t, -H, H, t, -H);
  }
  const gg = track(new THREE.BufferGeometry());
  gg.setAttribute('position', new THREE.Float32BufferAttribute(grid, 3));
  const gridMat = track(new THREE.LineBasicMaterial({ color: new THREE.Color(line), transparent: true, opacity: 0.55 }));
  S.add(new THREE.LineSegments(gg, gridMat));

  // One instanced mesh for every design: a single draw call.
  const geo = track(new THREE.SphereGeometry(0.2, 18, 12));
  const mat = track(new THREE.MeshLambertMaterial({ color: 0xffffff }));
  const mesh = new THREE.InstancedMesh(geo, mat, pts.length);
  const dummy = new THREE.Object3D();
  const drops = [];
  view.pos = [];
  pts.forEach((p, i) => {
    const v = new THREE.Vector3(n(p.x, ex.x), n(p.y, ex.y), n(p.z, ex.z));
    view.pos.push(v);
    dummy.position.copy(v); dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);
    mesh.setColorAt(i, ramp((p.c - ex.c[0]) / (ex.c[1] - ex.c[0])));
    drops.push(v.x, v.y, v.z, v.x, -H, v.z);       // drop line to the floor reads depth
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.computeBoundingSphere();
  S.add(mesh);
  view.mesh = mesh;
  const dg = track(new THREE.BufferGeometry());
  dg.setAttribute('position', new THREE.Float32BufferAttribute(drops, 3));
  S.add(new THREE.LineSegments(dg, track(new THREE.LineBasicMaterial({ color: new THREE.Color(muted), transparent: true, opacity: 0.35 }))));

  // Selected design: a ring that faces the camera, in the 2-D chart's "selected" colour.
  const mg = track(new THREE.SphereGeometry(0.36, 20, 14));
  const mm = track(new THREE.MeshBasicMaterial({ color: new THREE.Color(css('--c2')), wireframe: true }));
  view.marker = new THREE.Mesh(mg, mm);
  view.marker.visible = false;
  S.add(view.marker);

  // Axis titles and end values
  const ax = o.axes;
  const put = (s, x, y, z) => { s.position.set(x, y, z); S.add(s); };
  put(label(ax[0], ink, 0.8), 0, -H - 1.2, H + 0.9);
  put(label(short(ex.x[0]), muted, 0.6), -H, -H - 0.55, H + 0.5);
  put(label(short(ex.x[1]), muted, 0.6), H, -H - 0.55, H + 0.5);
  put(label(ax[1], ink, 0.8), -H - 1.1, H + 0.75, H);
  put(label(short(ex.y[0]), muted, 0.6), -H - 0.9, -H, H);
  put(label(short(ex.y[1]), muted, 0.6), -H - 0.9, H, H);
  put(label(ax[2], ink, 0.8), H + 1.3, -H - 1.2, 0);
  put(label(short(ex.z[0]), muted, 0.6), H + 0.8, -H - 0.55, -H);
  put(label(short(ex.z[1]), muted, 0.6), H + 0.8, -H - 0.55, H);

  view.cRange = ex.c;
}

function select(index) {
  if (!view.opts || !view.marker) return;
  view.opts.selected = index;
  const i = view.opts.points.findIndex(p => p.index === index);
  view.marker.visible = i >= 0;
  if (i >= 0) view.marker.position.copy(view.pos[i]);
  requestRender();
}

function legend(o) {
  let lg = view.host.parentElement.querySelector('.three-legend');
  if (!lg) {
    lg = document.createElement('div');
    lg.className = 'three-legend';
    view.host.parentElement.insertBefore(lg, view.host.nextSibling);
  }
  const fmt = v => short(v);
  lg.innerHTML = `<span>Net present cost: ${fmt(view.cRange[0])}<i class="colour-ramp" aria-hidden="true"></i>${fmt(view.cRange[1])}</span>`
    + `<span><i style="background:${css('--c2')}" aria-hidden="true"></i>Selected design</span>`
    + `<span>${o.points.length} designs · X: ${o.axes[0]} · Y: ${o.axes[1]} · Z: ${o.axes[2]}</span>`;
}

async function render(host, opts) {
  ensureRenderer(host);
  view.opts = opts;
  build(opts);
  legend(opts);
  resize();
  select(opts.selected);
  view.renderer.render(view.scene, view.camera);
}

function retheme() { if (view.opts && view.host && view.host.isConnected) render(view.host, view.opts); }

function stats() {
  const i = view.renderer ? view.renderer.info : null;
  return i ? { calls: i.render.calls, triangles: i.render.triangles,
               geometries: i.memory.geometries, textures: i.memory.textures } : null;
}

function destroy() {
  clearScene();
  if (view.ro) view.ro.disconnect();
  if (view.controls) view.controls.dispose();
  if (view.renderer) { view.renderer.dispose(); view.renderer.domElement.remove(); }
  Object.assign(view, { host: null, renderer: null, controls: null, ro: null });
}

window.Front3D = { render, select, retheme, stats, destroy, REVISION: THREE.REVISION };
