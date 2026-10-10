// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// One highlighting engine for everything: CodeMirror's language data (Lezer grammars,
// loaded lazily per language) + the `tok-*` class highlighter. The editor and the
// fenced code blocks of rendered Markdown therefore look the same.

import { LanguageDescription, type LanguageSupport } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { classHighlighter, highlightTree } from "@lezer/highlight";

// Names language-data does not match by file name alone.
const EXTRA: [RegExp, string][] = [
  [/^(Makefile|GNUmakefile|.*\.mk)$/, "CMake"],
  [/^\.?(bashrc|bash_profile|zshrc|profile|envrc)$/, "Shell"],
  [/\.(env|conf)$/, "Properties files"],
  [/^Cargo\.lock$/, "TOML"],
  [/\.(mdx)$/, "Markdown"],
];

function describeFile(name: string): LanguageDescription | null {
  const d = LanguageDescription.matchFilename(languages, name);
  if (d) return d;
  for (const [re, lang] of EXTRA) if (re.test(name)) return LanguageDescription.matchLanguageName(languages, lang, false);
  return null;
}

export async function languageForFile(name: string): Promise<LanguageSupport | null> {
  const d = describeFile(name);
  if (d?.name === "Markdown") {
    // fenced code blocks inside Markdown source get their own highlighting
    const { markdown, markdownLanguage } = await import("@codemirror/lang-markdown");
    return markdown({ base: markdownLanguage, codeLanguages: languages });
  }
  return d ? d.load() : null;
}

// A fence's word ("python", "py") or a file's name ("tool.py"): by language name or alias,
// else by file name, else as an extension.
async function languageByName(name: string): Promise<LanguageSupport | null> {
  const d = LanguageDescription.matchLanguageName(languages, name, true) ?? describeFile(name) ?? describeFile(`x.${name}`);
  return d ? d.load() : null;
}

/** Highlight `code` into the children of `into` using `tok-*` spans; false when the
 *  language is not known (the text stays as it was). */
export async function highlightInto(into: HTMLElement, code: string, lang: string): Promise<boolean> {
  const support = lang ? await languageByName(lang).catch(() => null) : null;
  if (!support) return false;
  const tree = support.language.parser.parse(code);
  const frag = document.createDocumentFragment();
  let pos = 0;
  highlightTree(tree, classHighlighter, (from, to, classes) => {
    if (from > pos) frag.append(code.slice(pos, from));
    const span = document.createElement("span");
    span.className = classes;
    span.textContent = code.slice(from, to);
    frag.append(span);
    pos = to;
  });
  if (pos < code.length) frag.append(code.slice(pos));
  into.replaceChildren(frag);
  return true;
}
