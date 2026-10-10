// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The full-screen viewer of an output widget's image or diagram (AC-56): it opens fitted to the
// window; two fingers pinch, the wheel (a trackpad's scroll or pinch) zooms around the pointer,
// a drag pans, a double click or tap toggles between fitted and 1:1 (twice the fit when that is
// 1:1 already); −, +, Fit and × (or Esc) in its corner.

const MIN = 0.05, MAX = 40;

let box = null;          // the viewer, made on first use
let st = null;           // {img, s, x, y, fit, onClose, pointers, last, moved}

function make() {
  box = document.createElement("div");
  box.id = "zoom";
  box.hidden = true;
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.innerHTML = `<div class="zstage"></div><p class="zcap"></p>
    <div class="ztools"><button type="button" data-z="out" aria-label="Zoom out" title="Zoom out">−</button>
    <button type="button" data-z="in" aria-label="Zoom in" title="Zoom in">+</button>
    <button type="button" data-z="fit" title="Fit to the window">Fit</button>
    <button type="button" data-z="close" aria-label="Close" title="Close (Esc)">✕</button></div>`;
  document.body.append(box);
  const stage = box.querySelector(".zstage");
  box.querySelector(".ztools").addEventListener("click", (e) => {
    const z = e.target.closest("button")?.dataset.z;
    if (z === "close") return close();
    const r = stage.getBoundingClientRect(), cx = r.width / 2, cy = r.height / 2;
    if (z === "in") zoomAt(st.s * 1.5, cx, cy);
    if (z === "out") zoomAt(st.s / 1.5, cx, cy);
    if (z === "fit") fit();
  });
  stage.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = stage.getBoundingClientRect();
    // a trackpad's pinch comes as a wheel with Ctrl, in small steps; a mouse wheel in large ones
    const k = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.002) * (e.deltaMode === 1 ? 16 : 1));
    zoomAt(st.s * k, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  stage.addEventListener("pointerdown", (e) => {
    stage.setPointerCapture(e.pointerId);
    st.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    st.moved = false;
  });
  stage.addEventListener("pointermove", (e) => {
    const p = st.pointers.get(e.pointerId);
    if (!p) return;
    const r = stage.getBoundingClientRect();
    if (st.pointers.size === 1) {
      st.x += e.clientX - p.x; st.y += e.clientY - p.y;
      if (Math.hypot(e.clientX - p.x, e.clientY - p.y) > 0) st.moved = true;
      p.x = e.clientX; p.y = e.clientY;
      return draw();
    }
    const [a, b] = [...st.pointers.values()];
    const before = { d: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
    p.x = e.clientX; p.y = e.clientY;
    const d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    st.moved = true;
    st.x += mx - before.mx; st.y += my - before.my;              // the fingers' middle carries the image
    if (before.d > 0) zoomAt(st.s * (d / before.d), mx - r.left, my - r.top);
  });
  const up = (e) => {
    if (!st.pointers.delete(e.pointerId) || st.pointers.size || st.moved) return;
    const now = performance.now(), r = stage.getBoundingClientRect();    if (st.last && now - st.last < 320) {                        // a double tap or click
      st.last = 0;
      const near = (a, b) => Math.abs(a - b) < 0.01;
      const to = !near(st.s, st.fit) ? st.fit : near(st.fit, 1) ? st.fit * 2 : 1;
      zoomAt(to, e.clientX - r.left, e.clientY - r.top);
    } else st.last = now;
  };
  stage.addEventListener("pointerup", up);
  stage.addEventListener("pointercancel", up);
  addEventListener("keydown", (e) => { if (!box.hidden && e.key === "Escape") { e.stopPropagation(); close(); } }, true);
  addEventListener("resize", () => { if (!box.hidden) fit(); });
}

function draw() {
  st.img.style.transform = `translate(${st.x}px, ${st.y}px) scale(${st.s})`;
}

// Scale to s keeping the stage point (px, py) where it is.
function zoomAt(s, px, py) {
  s = Math.min(MAX, Math.max(MIN, s));
  st.x = px - ((px - st.x) / st.s) * s;
  st.y = py - ((py - st.y) / st.s) * s;
  st.s = s;
  draw();
}

function fit() {
  const r = box.querySelector(".zstage").getBoundingClientRect();
  const w = st.img.naturalWidth || 300, h = st.img.naturalHeight || 150;
  st.fit = Math.min((r.width - 32) / w, (r.height - 32) / h);
  if (!st.vector) st.fit = Math.min(st.fit, 1);                 // a photo is not blown up past its pixels
  st.s = st.fit;
  st.x = (r.width - w * st.s) / 2; st.y = (r.height - h * st.s) / 2;
  draw();
}

/** Opens `src` (an image URL or an SVG data URL) full screen. caption: shown under it;
 *  vector: it may be fitted larger than its own size; onClose: when it closes. */
export function openZoom(src, { caption = "", alt = "", vector = false, onClose } = {}) {
  if (!box) make();
  const img = new Image();
  img.alt = alt;
  img.draggable = false;
  st = { img, s: 1, x: 0, y: 0, fit: 1, vector, onClose, pointers: new Map(), last: 0, moved: false };
  box.querySelector(".zstage").replaceChildren(img);
  box.querySelector(".zcap").textContent = caption;
  box.hidden = false;
  img.onload = fit;
  img.src = src;
  if (img.complete && img.naturalWidth) fit();
  box.querySelector('[data-z="close"]').focus({ preventScroll: true });
}

export function close() {
  if (!box || box.hidden) return;
  box.hidden = true;
  box.querySelector(".zstage").replaceChildren();
  const done = st?.onClose;
  st = null;
  done?.();
}

/** The viewer is open (the page's own Esc and keys leave it alone). */
export const zoomOpen = () => !!box && !box.hidden;
