// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Decode complete SSE events from arbitrarily fragmented UTF-8 string chunks.
export function sseData(receive) {
  let pending = "", data = [];
  return (chunk) => {
    pending += chunk;
    for (;;) {
      const end = pending.indexOf("\n");
      if (end < 0) return;
      const line = pending.slice(0, end).replace(/\r$/, "");
      pending = pending.slice(end + 1);
      if (!line) {
        if (data.length) receive(data.join("\n"));
        data = [];
      } else if (line === "data" || line.startsWith("data:")) {
        data.push(line.slice(5).replace(/^ /, ""));
      }
    }
  };
}
