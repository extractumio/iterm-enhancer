// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The files a pane's output names (AC-52, AC-56): where a printed path is (from the pane's folder
// and home, on its host), whether a file is there, and an image's URL through the Files proxy.
// Only a file that is there gets an underline, a click or an image button. A file is looked up by
// listing its folder through the proxy (nothing is read): one page answers every name of a
// folder of up to 500 entries; a larger one is asked by name.
import { absolutePath } from "./links.js";

const PANE_FOR = 5000;       // ms the pane's folder is taken as known (a "cd" moves it)
const AGAIN = [10000, 30000, 60000];   // ms before a file not there is looked for again, then again…
const KEEP_FOUND = 60000;    // ms a file found is taken as there (it may be deleted)
const AT_ONCE = 4;           // listings asked for at once
const PAGE = 500;            // entries of a folder's page (fbd's most)
const QUEUED = 200;          // folders waiting, at most: the newest asked first, the oldest dropped
const KEPT = 3000;           // answers kept, the oldest go first
const SAY_EVERY = 250;       // ms between two "files turned up" (the page marks links again)

/** paneInfo(): the shown pane's {cwd, home, host} or {error}, null when the Mac does not answer.
 *  fetchJson(url): the answer's JSON, null when it failed (tests give their own). */
export function paneFiles(paneInfo, fetchJson = (url) => fetch(url).then((r) => (r.ok ? r.json() : null), () => null)) {
  let pane = null, paneAt = -Infinity, asking = null;
  let found = new Map();     // host\npath -> {ok, at, tries, pending}
  const queue = [[], []];    // [images, text paths]: folders to list, {host, dir, names: Set}
  const listeners = new Set();
  let busy = 0, gen = 0, saying = 0, soon = 0;

  const say = () => { if (!saying) saying = setTimeout(() => { saying = 0; for (const fn of listeners) fn(); }, SAY_EVERY); };
  // the pane's folder, asked again at most every PANE_FOR ms; a new one means new places
  function refresh() {
    if (asking || performance.now() - paneAt < PANE_FOR) return asking;
    const mine = gen;
    asking = paneInfo().then((info) => {
      asking = null;
      if (mine !== gen) return;
      paneAt = performance.now();
      const moved = JSON.stringify([pane?.cwd, pane?.home, pane?.host, pane?.error]) !== JSON.stringify([info?.cwd, info?.home, info?.host, info?.error]);
      pane = info;
      if (moved) say();
    }, () => { asking = null; });
    return asking;
  }
  const query = (params, host) => { const q = new URLSearchParams(params); if (host) q.set("host", host); return q; };
  const placeOf = (value, p = pane) => {
    const path = p && !p.error ? absolutePath(value, p.cwd, p.home) : null;
    return path && { host: p.host ?? null, path, dir: path.slice(0, path.lastIndexOf("/")) || "/", name: path.slice(path.lastIndexOf("/") + 1) };
  };
  const keyOf = (host, path) => `${host ?? ""}\n${path}`;

  function remember(host, dir, name, ok) {
    const k = keyOf(host, dir === "/" ? `/${name}` : `${dir}/${name}`), was = found.get(k);
    found.delete(k);
    found.set(k, { ok, at: performance.now(), tries: ok ? 0 : (was?.tries ?? 0) + 1 });
    if (found.size > KEPT) found.delete(found.keys().next().value);
  }
  async function list(job) {
    const isFile = (e) => e.k === "f" || e.k === "l";
    const ask = (params) => fetchJson(`/fb/api/ls?${query({ path: job.dir, limit: String(PAGE), ...params }, job.host)}`);
    let page = await ask({});
    for (let tries = 0; page?.status === "loading" && tries < 3; tries++) {         // a large folder still being read
      await new Promise((r) => setTimeout(r, 400));
      page = await ask({});
    }
    if (!page || page.status !== "ready") return [...job.names].map((n) => [n, false]);
    if (page.total <= page.entries.length) {
      const files = new Set(page.entries.filter(isFile).map((e) => e.n));
      return [...job.names].map((n) => [n, files.has(n)]);
    }
    return Promise.all([...job.names].map(async (n) => {                            // more than a page: by name
      const one = await ask({ filter: n });
      return [n, !!one?.entries?.some((e) => e.n === n && isFile(e))];
    }));
  }
  function next() {
    while (busy < AT_ONCE) {
      const job = queue[0].pop() ?? queue[1].pop();
      if (!job) return;
      const mine = gen;
      busy++;
      list(job).catch(() => [...job.names].map((n) => [n, false])).then((answers) => {
        busy--;
        if (mine === gen) {                                          // not another pane's meanwhile
          for (const [n, ok] of answers) remember(job.host, job.dir, n, ok);
          if (answers.some(([, ok]) => ok)) say();
        }
        next();
      });
    }
  }
  function enqueue(place, urgent) {
    const k = keyOf(place.host, place.path);
    found.set(k, { ...(found.get(k) ?? { ok: false, tries: 0 }), pending: true });           // asked: not again meanwhile
    const q = queue[urgent ? 0 : 1];
    let job = q.find((j) => j.dir === place.dir && j.host === place.host);
    if (job) q.splice(q.indexOf(job), 1);
    else job = { host: place.host, dir: place.dir, names: new Set() };
    job.names.add(place.name);
    q.push(job);                                                     // the newest first
    if (q.length > QUEUED) {                                         // the oldest dropped: asked anew when marked again
      const old = q.shift();
      for (const n of old.names) found.delete(keyOf(old.host, old.dir === "/" ? `/${n}` : `${old.dir}/${n}`));
    }
    soon ||= setTimeout(() => { soon = 0; next(); }, 0);             // once the marking pass named all it sees
  }

  /** "yes": its file is there; "no": not, or not known yet; or {error}: the pane's folder is not
   *  known (a click says why). Asked in the background when not known (listeners hear when one
   *  turns out to be there). urgent: before text paths (an image button). */
  function state(value, urgent = false) {
    refresh();
    if (!pane) return "no";
    if (pane.error) return { error: pane.error };
    const place = placeOf(value);
    if (!place) return { error: `Cannot tell where ${value} is: this session's folder is not known.` };
    const k = found.get(keyOf(place.host, place.path));
    const stale = !k || (!k.pending && performance.now() - k.at > (k.ok ? KEEP_FOUND : AGAIN[Math.min(AGAIN.length, k.tries) - 1]));
    if (stale) enqueue(place, urgent);
    return k?.ok ? "yes" : "no";
  }
  return {
    state,
    /** Whether the file a printed path names is there, as far as known now. */
    there: (value, urgent = false) => state(value, urgent) === "yes",
    /** Another pane is shown: its folder and files are asked again. */
    forget() { gen++; pane = null; paneAt = -Infinity; asking = null; found = new Map(); queue[0].length = queue[1].length = 0; },
    /** {url, path} of the image a printed path names, or {error}. */
    async resolve(value) {
      if (!pane || performance.now() - paneAt > PANE_FOR) { paneAt = -Infinity; await refresh(); }
      if (!pane) return { error: "the Mac does not answer" };
      if (pane.error) return { error: pane.error };
      const place = placeOf(value);
      return place ? { url: `/fb/api/raw?${query({ path: place.path }, place.host)}`, path: place.path } : { error: "this session's folder is not known" };
    },
    /** fn() when files turned out to be there or the pane's folder changed (at most every 250 ms). */
    listen(fn) { listeners.add(fn); },
  };
}
