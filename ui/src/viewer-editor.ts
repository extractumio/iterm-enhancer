// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// CodeMirror presentation, shortcuts and the document's original line separator.

import { EditorState, type Compartment, type Extension } from "@codemirror/state";
import { EditorView, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection, keymap,
  highlightSpecialChars, rectangularSelection, crosshairCursor } from "@codemirror/view";
import { syntaxHighlighting, foldGutter, bracketMatching, indentOnInput, foldKeymap, indentUnit } from "@codemirror/language";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { classHighlighter } from "@lezer/highlight";
import { basename, type FileView } from "./api";
import { isMarkdown } from "./markdown";

const cmTheme = EditorView.theme({
  "&": { height: "100%", backgroundColor: "transparent", color: "var(--fg)" },
  ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55" },
  ".cm-content": { caretColor: "var(--cursor)" },
  ".cm-gutters": { backgroundColor: "var(--bg)", color: "var(--fg-faint)", border: "none" },
  ".cm-activeLineGutter": { backgroundColor: "var(--bg-hover)", color: "var(--fg-dim)" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--bg-hover) 60%, transparent)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "var(--bg-sel) !important" },
  ".cm-selectionMatch": { backgroundColor: "color-mix(in srgb, var(--accent) 22%, transparent)" },
  ".cm-foldGutter span": { color: "var(--fg-faint)" },
  ".cm-panels": { backgroundColor: "var(--bg-2)", color: "var(--fg)" },
  ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, var(--warn) 35%, transparent)" },
  ".cm-cursor": { borderLeftColor: "var(--cursor)" },
});

export function editorExtensions(path: string, f: FileView, lang: Compartment, save: () => void): Extension[] {
  const wrap = isMarkdown(path) || /\.(txt|log)$/i.test(path);
  return [
    lineNumbers(), foldGutter(), highlightSpecialChars(), history(), drawSelection(),
    indentOnInput(), bracketMatching(), rectangularSelection(), crosshairCursor(),
    highlightActiveLine(), highlightActiveLineGutter(), highlightSelectionMatches(),
    syntaxHighlighting(classHighlighter),
    keymap.of([
      { key: "Mod-s", preventDefault: true, run: () => { save(); return true; } },
      ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...foldKeymap, indentWithTab,
    ]),
    lang.of([]),
    wrap ? EditorView.lineWrapping : [],
    EditorState.readOnly.of(!f.writable),
    EditorState.tabSize.of(4),
    EditorState.lineSeparator.of(f.text?.match(/\r\n|\r|\n/)?.[0] ?? "\n"),
    indentUnit.of(/\.(go|mk)$|^Makefile$/.test(basename(path)) ? "\t" : "    "),
    cmTheme,
  ];
}
