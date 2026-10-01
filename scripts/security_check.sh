#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# security_check.sh — AC-07: refused requests against the installed fbd.
port=${FB_PORT:-47821}; b="http://127.0.0.1:$port"
tok=$(cat "${FB_APP_DIR:-$HOME/Library/Application Support/iterm-filebrowser}/token")
fail=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then echo "ok   $1 → $3"; else echo "FAIL $1 → $3 (want $2)"; fail=1; fi; }
check "no token"          401 "$(curl -s -o /dev/null -w '%{http_code}' "$b/api/ls?path=$HOME")"
check "wrong token"       401 "$(curl -s -o /dev/null -w '%{http_code}' -H 'X-FB-Token: nope' "$b/api/ls?path=$HOME")"
check "bad host"          403 "$(curl -s -o /dev/null -w '%{http_code}' -H "Host: evil.example:$port" -H "X-FB-Token: $tok" "$b/api/state")"
check "bad origin"        403 "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H 'Origin: http://evil.example' -H 'Content-Type: application/json' -H "X-FB-Token: $tok" -d '{}' "$b/api/prefs")"
check "bad content type"  415 "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H 'Content-Type: text/plain' -H "X-FB-Token: $tok" -d '{}' "$b/api/prefs")"
check "internal w/o secret" 401 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "X-FB-Token: $tok" -d '{}' "$b/internal/state")"
check "token in query on PUT" 401 "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H 'Content-Type: application/json' -d '{}' "$b/api/prefs?t=$tok")"
check "relative path"     400 "$(curl -s -o /dev/null -w '%{http_code}' -H "X-FB-Token: $tok" "$b/api/file?path=../../etc/passwd")"
check "valid request"     200 "$(curl -s -o /dev/null -w '%{http_code}' -H "X-FB-Token: $tok" "$b/api/state")"
[ $fail = 0 ] && echo PASS || { echo FAIL; exit 1; }
