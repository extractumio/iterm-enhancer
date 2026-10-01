#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# make_big_dir.sh DIR COUNT — create COUNT empty files f000000.txt… in DIR (for AC-02 checks)
set -euo pipefail
dir=${1:?dir}; n=${2:-500000}
mkdir -p "$dir"
python3 - "$dir" "$n" <<'PY'
import os, sys
d, n = sys.argv[1], int(sys.argv[2])
for i in range(n):
    p = os.path.join(d, "f%06d.txt" % i)
    if not os.path.exists(p):
        open(p, "w").close()
PY
echo "$dir: $(ls -f "$dir" | wc -l | tr -d ' ') entries"
