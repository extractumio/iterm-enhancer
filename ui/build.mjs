// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Bundles the panel into dist/ (embedded into fbd by rust-embed).
// Language grammars are split into lazy chunks, so the first load stays small.
import * as esbuild from "esbuild";
import { cpSync, rmSync } from "node:fs";

const watch = process.argv.includes("--watch");
rmSync("dist", { recursive: true, force: true });
cpSync("public", "dist", { recursive: true });
const ctx = await esbuild.context({
  entryPoints: ["src/main.ts"],
  bundle: true,
  splitting: true,
  format: "esm",
  outdir: "dist",
  chunkNames: "chunks/[name]-[hash]",
  minify: !watch,
  sourcemap: watch ? "inline" : false,
  target: ["safari16"],
  loader: { ".svg": "text" },  // icons are inlined as markup (src/icons.ts)
  logLevel: "info",
});
if (watch) await ctx.watch();
else { await ctx.rebuild(); await ctx.dispose(); }
