// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The image files named in a pane's output (AC-56): where a printed path is (from the pane's
// folder and home, on its host), whether it is there, and its URL through the Files proxy.
import { absolutePath } from "./links.js";

const PANE_FOR = 5000;       // ms the pane's folder is taken as known (a "cd" may move it)

/** paneInfo(): the shown pane's {cwd, home, host} or {error}, null when the Mac does not answer. */
export function imageFiles(paneInfo) {
  let known = null;          // {at, info}
  async function where(value) {
    if (!known || performance.now() - known.at > PANE_FOR) known = { at: performance.now(), info: await paneInfo() };
    const pane = known.info;
    if (!pane) return { error: "the Mac does not answer" };
    if (pane.error) return { error: pane.error };
    const path = absolutePath(value, pane.cwd, pane.home);
    return path ? { path, host: pane.host ?? null } : { error: "this session's folder is not known" };
  }
  const query = (params, host) => { const q = new URLSearchParams(params); if (host) q.set("host", host); return q; };
  return {
    /** Another pane is shown: its folder is asked again. */
    forget() { known = null; },
    /** {url, path} of the image a printed path names, or {error}. */
    async resolve(value) {
      const w = await where(value);
      return w.error ? w : { url: `/fb/api/raw?${query({ path: w.path }, w.host)}`, path: w.path };
    },
    /** The file is there (a listing of its folder, filtered by its name: no image is read). */
    async exists(value) {
      const w = await where(value);
      if (w.error) return false;
      const cut = w.path.lastIndexOf("/"), dir = w.path.slice(0, cut) || "/", name = w.path.slice(cut + 1);
      for (let tries = 0; tries < 3; tries++) {
        const page = await fetch(`/fb/api/ls?${query({ path: dir, filter: name, limit: "50" }, w.host)}`)
          .then((r) => (r.ok ? r.json() : null), () => null);
        if (!page || page.status === "error") return false;
        if (page.status === "ready") return page.entries.some((e) => e.n === name && e.k !== "d");
        await new Promise((r) => setTimeout(r, 400));               // a large folder still being read
      }
      return false;
    },
  };
}
