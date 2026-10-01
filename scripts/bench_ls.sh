#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# bench_ls.sh DIR [RUNS] — AC-02: cold (not in fbd cache) first page and cached random pages.
# Runs a private fbd on port 47899 so the installed one is not disturbed.
set -euo pipefail
dir=$(cd "${1:?dir}" && pwd -P); runs=${2:-10}
bin=${FB_BIN:-$(dirname "$0")/../fbd/target/release/fbd}
app=$(mktemp -d)   # private token/workspaces: never touch the live fbd's files
trap 'rm -rf "$app"' EXIT
url="http://127.0.0.1:47899/api/ls?path=$dir"
cold=(); warm=()
for i in $(seq "$runs"); do
  FB_PORT=47899 FB_APP_DIR="$app" "$bin" 2>/dev/null & pid=$!
  for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:47899/" && break; sleep 0.05; done
  tok=$(cat "$app/token")
  cold+=("$(curl -s -o /dev/null -w '%{time_total}' -H "X-FB-Token: $tok" "$url&limit=500")")
  total=$(curl -s -H "X-FB-Token: $tok" "$url&limit=1" | python3 -c 'import json,sys;print(json.load(sys.stdin)["total"])')
  warm+=("$(curl -s -o /dev/null -w '%{time_total}' -H "X-FB-Token: $tok" "$url&offset=$((RANDOM * RANDOM % total))&limit=500")")
  rss=$(ps -o rss= -p $pid)
  kill $pid; wait $pid 2>/dev/null || true
done
p95() { printf '%s\n' "$@" | sort -n | awk '{a[NR]=$1} END {i=int(NR*0.95); if(i<1)i=1; printf "%d", a[i]*1000}'; }
c=$(p95 "${cold[@]}"); w=$(p95 "${warm[@]}")
verdict=$([ "$c" -lt 2500 ] && [ "$w" -lt 50 ] && [ "$rss" -lt 153600 ] && echo PASS || echo FAIL)
echo "entries=$total cold_p95_ms=$c page_p95_ms=$w rss_mb=$((rss/1024)) $verdict"
