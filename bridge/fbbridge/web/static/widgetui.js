// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The output widgets' own elements (AC-56). A widget lives in a shadow root on a terminal row
// (widgets.js), so the row's text, its selection and copying, the links' underlines and the
// cursor's maths see only the terminal's own text. Content from the terminal is put in as text,
// or as the panel's Markdown (raw HTML off); a diagram is an image, so nothing in it runs.
import { icon } from "./icons.js";
import { openZoom } from "./zoom.js";

let lib = null, tries = 0;
/** The renderers: fbd's renderers.js through the page's /fb proxy, loaded on first need. */
export function renderers() {
  // a failed module stays failed under its URL: a new try asks under another
  return (lib ??= import(tries ? `/fb/renderers.js?try=${tries}` : "/fb/renderers.js").catch((e) => {
    lib = null;                                    // asked again next time (fbd may come back)
    tries++;
    throw new Error(`The renderer did not load (${e.message}); is iterm-enhancer's backend running on the Mac?`);
  }));
}

// The widgets' style sheet, shared by every shadow root (widgets.css), loaded with the page:
// widgets are attached only once it is in, as one drawn unstyled first changes its height
// after the page placed the view.
let sheet = null, loaded = null;
export function loadStyles() {
  sheet ??= new CSSStyleSheet();
  return (loaded ??= fetch("widgets.css").then((r) => r.text()).then((css) => sheet.replace(css))
    .catch(() => { /* unstyled, but working */ }));
}

/** The shadow root of row element `el` (made once: a row's text shows through its slot). */
export function hostOf(el, onClick) {
  if (el.shadowRoot) return el.shadowRoot;
  const root = el.attachShadow({ mode: "open" });
  root.adoptedStyleSheets = [sheet];
  root.append(document.createElement("slot"));
  root.addEventListener("click", (e) => {
    e.stopPropagation();                           // the terminal's own clicks (⌘-click, ⌥-click) are not for it
    const a = e.target.closest("a[href]");
    if (a) {
      e.preventDefault();                          // the page never navigates away
      if (/^https?:/i.test(a.getAttribute("href"))) window.open(a.href, "_blank", "noopener,noreferrer");
      return;
    }
    const b = e.target.closest("[data-act]");
    if (b) onClick(b, root);
  });
  return root;
}

/** The event came from inside a widget (its taps are not the terminal's). */
export const inWidget = (e) => e.composedPath?.().some((n) => n instanceof Element && n.hasAttribute("data-wg"));

const NAMES = { code: "Code", markdown: "Markdown", mermaid: "Diagram" };

function button(act, label, title, iconName) {
  const b = document.createElement("button");
  b.type = "button";
  b.dataset.act = act;
  if (iconName) b.append(icon(iconName, 14));
  if (label) b.append(label);
  if (title) { b.title = title; b.setAttribute("aria-label", title); }
  return b;
}

/** A region's widget: its bar (name, Raw | rendered, Copy, Full screen) and its body, filled
 *  when the renderer answers. wg.draw() draws the body (once); the page calls it when the widget
 *  is first shown rendered. ctx: {key, dark, change(fn), raw()}: change runs a change to the
 *  page so the reader's place is kept; raw: it could not be drawn, so the region shows as text,
 *  with the error in the bar, and the widget is marked failed (data-failed: made again on the
 *  next try). */
export function regionWidget(region, ctx) {
  const wg = document.createElement("div");
  wg.className = "wg";
  wg.dataset.wg = "";
  wg.dataset.key = ctx.key;
  wg.dataset.kind = region.kind;
  const bar = document.createElement("div");
  bar.className = "bar";
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = region.kind === "code" && region.lang ? region.lang : NAMES[region.kind];
  const seg = document.createElement("span");
  seg.className = "seg";
  seg.append(button("raw", "Raw", ""), button("on", NAMES[region.kind], ""));
  const err = document.createElement("span");
  err.className = "err";
  err.hidden = true;
  bar.append(name, seg, button("copy", "Copy", "Copy the source", "copy"));
  if (region.kind === "mermaid") bar.append(button("zoom", "", "Full screen", "maximize-2"));
  bar.append(err);
  const body = document.createElement("div");
  body.className = `body ${region.kind}`;
  wg.append(bar, body);
  const say = (e) => ctx.change(() => { err.textContent = e.message || String(e); err.hidden = false; });
  const fail = (e) => {
    say(e);
    if (region.kind === "code") return;            // code shows as plain text; the others would show nothing
    wg.dataset.failed = "";
    ctx.raw();
  };
  wg.draw = () => {
    wg.draw = () => {};
    if (region.kind === "code") {
      const pre = document.createElement("pre"), code = document.createElement("code");
      code.textContent = region.text;
      pre.append(code);
      body.append(pre);
      renderers().then((r) => r.highlight(code, region.text, region.lang)).catch(fail);
    } else if (region.kind === "markdown") {
      renderers().then(async (r) => {
        ctx.change(() => { body.innerHTML = r.markdownHtml(region.text); });   // the panel's rules: raw HTML is off
        for (const code of body.querySelectorAll("pre > code[class*='language-']")) {
          const lang = [...code.classList].find((c) => c.startsWith("language-")).slice(9);
          // a diagram that does not draw keeps its source, and its bar says why
          if (lang === "mermaid") void diagram(code.textContent, ctx.dark).then((img) => ctx.change(() => code.parentElement.replaceWith(img)), say);
          else await r.highlight(code, code.textContent, lang);
        }
      }).catch(fail);
    } else {
      diagram(region.text, ctx.dark).then((img) => ctx.change(() => body.replaceChildren(img)), fail);
    }
  };
  return wg;
}

/** A Mermaid diagram as an image (SVG data URL), opened full screen on a click. */
async function diagram(source, dark) {
  const svg = await (await renderers()).mermaidSvg(source, dark);
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const root = doc.documentElement;
  if (root.nodeName !== "svg") throw new Error("The diagram did not draw.");
  // its own size, so the image has one: Mermaid draws it at 100% width with a max-width
  const [, , w, h] = (root.getAttribute("viewBox") ?? "").split(/[\s,]+/).map(Number);
  if (w > 0 && h > 0) { root.setAttribute("width", String(w)); root.setAttribute("height", String(h)); }
  root.removeAttribute("style");
  const img = new Image();
  img.className = "diagram";
  img.alt = "Mermaid diagram";
  img.dataset.act = "zoom";
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(root));
  await img.decode().catch(() => {});             // in place at its size at once: the page does not move later
  return img;
}

/** Opens the widget's diagram (or the one clicked in a Markdown widget) full screen. */
export function zoomDiagram(b, onClose) {
  const img = b.tagName === "IMG" ? b : b.closest(".wg")?.querySelector("img.diagram");
  if (img) openZoom(img.src, { alt: img.alt, vector: true, onClose });
}

/** The image buttons after a row's text, one per image named in it, and the images opened
 *  below it. paths: [{value}]. */
export function imageButtons(paths) {
  const span = document.createElement("span");
  span.className = "wgi";
  span.dataset.wg = "";
  paths.forEach((p, i) => {
    const b = button("img", "", `Show the image ${p.value}`, "image");
    b.dataset.i = String(i);
    b.dataset.path = p.value;
    span.append(b);
  });
  const figs = document.createElement("div");
  figs.className = "figs";
  figs.dataset.wg = "";
  return [span, figs];
}

/** The image at got.url, loaded before it is shown (so it takes its place at its size at once):
 *  got with img, or with the reason it cannot be shown (fbd's message when it has one). */
export async function loadImage(got) {
  if (!got.url) return got;
  const img = new Image();
  img.alt = got.path ?? "";
  img.dataset.act = "zoomimg";
  img.src = got.url;
  await img.decode().catch(() => {});
  if (img.naturalWidth) return { ...got, img };
  // the browser does not say why: ask once more for fbd's message
  const why = await fetch(got.url).then(async (r) => (r.ok ? "not an image the browser can show"
    : (await r.json().catch(() => ({}))).message ?? `HTTP ${r.status}`), (e) => e.message);
  return { ...got, error: why };
}

/** An image shown in the history: centered, fitted to half the terminal's width and height,
 *  opened full screen on a click; its caption names the file it is (a relative path is taken
 *  from the pane's folder now, which may not be where it was printed). */
export function figure(path, { img, error, path: file }) {
  const fig = document.createElement("figure");
  fig.dataset.path = path;
  const cap = document.createElement("figcaption");
  const name = document.createElement("span");
  name.textContent = error ? `${file ?? path}: ${error}` : file ?? path;
  if (error) name.className = "err";
  cap.append(name, button("unimg", "", "Close the image", "x"));
  // the one loaded, moved (never asked for again); a second row of the same text gets a copy
  if (img && !error) fig.append(img.isConnected ? img.cloneNode() : img);
  fig.append(cap);
  return fig;
}

export function zoomImage(img, onClose) {
  openZoom(img.src, { alt: img.alt, caption: img.alt, onClose });
}
