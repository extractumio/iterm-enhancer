// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The viewer window's path bar (AC-26): the active tab's full path, selectable, with a copy
// button. iTerm2's own address bar cannot be hidden, so this is where the path is read.

import { copyIcon } from "./icons";

export class PathBar {
  private path = "";
  private text: HTMLElement;

  constructor(el: HTMLElement, copy: (text: string) => void) {
    el.innerHTML = `<span class="path"></span><button class="icon" title="Copy full path">${copyIcon}</button>`;
    this.text = el.querySelector(".path")!;
    el.querySelector("button")!.onclick = () => { if (this.path) copy(this.path); };
  }

  show(path: string | null) {
    this.path = path ?? "";
    this.text.textContent = this.path;
    this.text.title = this.path;
    document.title = this.path || "Files";
  }
}
