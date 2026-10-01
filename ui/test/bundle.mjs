// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Bundles one UI module for node (no DOM) and imports it, for the unit tests.
import * as esbuild from "esbuild";

export async function load(entry, options = {}) {
  const out = await esbuild.build({
    entryPoints: [entry], bundle: true, format: "esm", platform: "neutral", write: false,
    mainFields: ["module", "main"], logLevel: "silent", loader: { ".svg": "text" }, ...options,
  });
  return import("data:text/javascript;base64," + Buffer.from(out.outputFiles[0].text).toString("base64"));
}
