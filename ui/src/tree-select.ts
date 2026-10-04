// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Range selection loads one missing page at a time and yields to input and painting.
export async function selectRange(
  first: number, last: number, read: (index: number) => Promise<string | null>,
  alive: () => boolean, add: (path: string) => void,
): Promise<boolean> {
  for (let i = first; i <= last; i++) {
    if (!alive()) return false;
    const path = await read(i);
    if (!alive()) return false;
    if (path) add(path);
    if ((i - first + 1) % 500 === 0) await new Promise((resolve) => setTimeout(resolve, 0));
  }
  return alive();
}
