#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# security_check.sh — AC-07: refused requests against the installed fbd.
port=${FB_PORT:-47821}; b="http://127.0.0.1:$port"
if [ -z "${FB_APP_DIR:-}" ]; then
  echo "Integration tests require an isolated FB_APP_DIR; the live installation is refused." >&2
  echo "Run it with the iTerm2 checks: python3 scripts/e2e_isolated.py, or against a private fbd:" >&2
  echo "  FB_PORT=<its port> FB_APP_DIR=<its short folder> scripts/security_check.sh" >&2
  exit 1
fi
app="$FB_APP_DIR"
python3 - "$app" <<'PY'
from pathlib import Path
import sys
if Path(sys.argv[1]).resolve() == (Path.home() / '.iterm-filebrowser/state').resolve():
    raise SystemExit('Integration tests refuse the live state directory, including symlinks')
PY
if [ "$?" != 0 ]; then exit 1; fi
tok=$(cat "$app/token")
sock() { curl -s -o /dev/null -w '%{http_code}' --unix-socket "$app/fbd.sock" "$@"; }
fail=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then echo "ok   $1 → $3"; else echo "FAIL $1 → $3 (want $2)"; fail=1; fi; }
check "no token"          401 "$(curl -s -o /dev/null -w '%{http_code}' "$b/api/ls?path=$HOME")"
check "wrong token"       401 "$(curl -s -o /dev/null -w '%{http_code}' -H 'X-FB-Token: nope' "$b/api/ls?path=$HOME")"
check "bad host"          403 "$(curl -s -o /dev/null -w '%{http_code}' -H "Host: evil.example:$port" -H "X-FB-Token: $tok" "$b/api/state")"
check "bad origin"        403 "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H 'Origin: http://evil.example' -H 'Content-Type: application/json' -H "X-FB-Token: $tok" -d '{}' "$b/api/prefs")"
check "bad content type"  415 "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H 'Content-Type: text/plain' -H "X-FB-Token: $tok" -d '{}' "$b/api/prefs")"
check "internal over TCP" 404 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "X-FB-Token: $tok" -d '{}' "$b/internal/state")"
check "socket: internal w/o secret" 401 "$(sock -X POST -H 'Content-Type: application/json' -d '{}' http://fbd/internal/state)"
check "socket: error w/o secret" 401 "$(sock -X POST -H 'Content-Type: application/json' -d '{"message":"x"}' http://fbd/internal/error)"
check "socket: setup w/o secret" 401 "$(sock -X POST -H 'Content-Type: application/json' -d 'null' http://fbd/internal/setup)"
check "socket: inventory w/o secret" 401 "$(sock -X POST -H 'Content-Type: application/json' -d '{}' http://fbd/internal/liveness)"
check "setup over TCP" 404 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "X-FB-Token: $tok" -d 'null' "$b/internal/setup")"
check "recovery w/o token" 401 "$(curl -s -o /dev/null -w '%{http_code}' "$b/api/recovery")"
check "restore w/o token" 401 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$b/api/recovery/restore")"
check "socket: recovery w/o secret" 401 "$(sock http://fbd/internal/recovery)"
check "socket: capture w/o secret" 401 "$(sock -X POST -H 'Content-Type: application/json' -d '{}' http://fbd/internal/recovery/capture)"
check "recovery over TCP" 404 "$(curl -s -o /dev/null -w '%{http_code}' -H "X-FB-Token: $tok" "$b/internal/recovery")"
check "web switch w/o token" 401 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{"action":"on"}' "$b/api/web")"
check "socket: web status w/o secret" 401 "$(sock -X POST -H 'Content-Type: application/json' -d '{}' http://fbd/internal/web)"
check "web status over TCP" 404 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "X-FB-Token: $tok" -d '{}' "$b/internal/web")"
for action in startup startup/begin startup/finish lifecycle exit; do
  check "socket: $action w/o secret" 401 "$(sock -X POST -H 'Content-Type: application/json' -d '{}' "http://fbd/internal/recovery/$action")"
  check "$action over TCP" 404 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "X-FB-Token: $tok" -d '{}' "$b/internal/recovery/$action")"
done
check "socket: health"    200 "$(sock http://fbd/health)"
check "hello needs no token" 200 "$(curl -s -o /dev/null -w '%{http_code}' "$b/api/hello?n=00112233445566778899aabbccddeeff")"
check "device file"       400 "$(curl -s -o /dev/null -w '%{http_code}' -m 2 -H "X-FB-Token: $tok" "$b/api/raw?path=/dev/zero")"
check "private root"      700 "$(stat -f %Lp "$app/..")"
check "token in query on PUT" 401 "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H 'Content-Type: application/json' -d '{}' "$b/api/prefs?t=$tok")"
check "relative path"     400 "$(curl -s -o /dev/null -w '%{http_code}' -H "X-FB-Token: $tok" "$b/api/file?path=../../etc/passwd")"
check "valid request"     200 "$(curl -s -o /dev/null -w '%{http_code}' -H "X-FB-Token: $tok" "$b/api/state")"
[ $fail = 0 ] && echo PASS || { echo FAIL; exit 1; }
