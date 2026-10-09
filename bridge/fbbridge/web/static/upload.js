// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Files to the shown pane (AC-52): a pasted image, a file from "Upload" on the hot keys, files
// dropped on the page. Each is saved where the pane's shell runs (this Mac or its host), keeping
// a file's name, and its path is pasted as text without Enter: programs such as Claude Code take
// a file by its path. Dropped files go one at a time (the bridge takes two at once) and their
// paths are pasted together.
import { shellWord } from "./input.js";

export function uploads({ sid, paste, toast, hideToast }) {
  /** Saves one file: {path} or {error}. */
  async function save(blob, what, url, from) {
    const r = await fetch(url + encodeURIComponent(from), { method: "POST", headers: { "Content-Type": blob.type || "application/octet-stream" }, body: blob })
      .catch(() => null);
    const text = r ? await r.text().catch(() => "") : "";
    let m = {};
    try { m = JSON.parse(text); } catch { m = { error: text.trim() || undefined }; }   // the server's own refusals are plain text
    if (!r?.ok || !m.path) return { error: m.error ?? (r ? `The ${what} was not uploaded (${r.status}).` : "The Mac does not answer.") };
    return { path: m.path };
  }

  // The paths saved are pasted; a failure stops the rest and stays on screen.
  async function send(list, what) {
    const from = sid();
    if (!from) return toast("Open a session first.");
    const paths = [];
    let error = null;
    for (const [i, [blob, url]] of list.entries()) {
      toast(list.length > 1 ? `Uploading ${i + 1} of ${list.length}…` : `Uploading the ${what}…`, 60000);
      ({ path: paths[i], error } = await save(blob, what, url, from));
      if (error) { paths.pop(); break; }
    }
    if (paths.length && sid() !== from) return toast(`The ${what} was saved, but another session is shown now: nothing was pasted.`, 6000);
    if (paths.length) paste(paths.map(shellWord).join(" "));
    if (!error) return hideToast();
    toast(list.length > 1 ? `${paths.length} of ${list.length} uploaded (their paths pasted); ${list[paths.length][0].name}: ${error} The rest were not sent.` : error, 8000);
  }

  const fileUrl = (f) => `/upload?name=${encodeURIComponent(f.name)}&sid=`;
  return {
    pasteImage: (blob) => send([[blob, "/paste?sid="]], "image"),
    uploadFile: (file) => send([[file, fileUrl(file)]], "file"),
    /** Files dropped anywhere on the page (a drop the page did not take would open the file in
     *  the browser and leave the page, with any unsaved edits in View). */
    bindDrop(target) {
      const hasFiles = (e) => [...(e.dataTransfer?.types ?? [])].includes("Files");
      document.addEventListener("dragover", (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        target.classList.add("dropping");
      });
      document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) target.classList.remove("dropping"); });
      document.addEventListener("drop", (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        target.classList.remove("dropping");
        const items = [...e.dataTransfer.items].filter((it) => it.kind === "file");
        if (items.some((it) => it.webkitGetAsEntry?.()?.isDirectory)) return toast("A folder cannot be uploaded: drop its files.", 6000);
        const files = items.map((it) => it.getAsFile()).filter(Boolean);
        if (files.length) void send(files.map((f) => [f, fileUrl(f)]), "file");
      });
    },
  };
}
