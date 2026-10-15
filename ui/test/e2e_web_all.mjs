// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The web app's browser checks for e2e_panel.mjs, in the order they run: each opens its own
// pages on the real page files against a fake bridge.

import { webApp } from "./e2e_web_app.mjs";
import { webBoxNav } from "./e2e_web_boxnav.mjs";
import { webClip } from "./e2e_web_clip.mjs";
import { webCtxTrace } from "./e2e_web_ctxtrace.mjs";
import { webEdit } from "./e2e_web_edit.mjs";
import { webMarks } from "./e2e_web_marks.mjs";
import { webMerge } from "./e2e_web_merge.mjs";
import { webTerm } from "./e2e_web_term.mjs";
import { webTouch } from "./e2e_web_touch.mjs";
import { webTrace } from "./e2e_web_trace.mjs";
import { webWidgets } from "./e2e_web_widgets.mjs";

export async function webAll(browser) {
  for (const run of [webTerm, webApp, webMerge, webTrace, webCtxTrace, webClip, webWidgets, webMarks, webTouch, webEdit, webBoxNav]) await run(browser);
}
