# Security

`fbd` gives a web view read and write access to your files, so it is built to be reachable
only by the Files panel:

| Threat | Defense |
|---|---|
| Other machines | binds `127.0.0.1` only |
| Web pages in any browser (CSRF, DNS rebinding) | random 128-bit token (file mode 0600) on every request; exact `Host` check; writes need `Content-Type: application/json` and a same-origin or absent `Origin`; no CORS headers |
| Local processes of other users | token file readable only by you |
| The panel page itself (a malicious README or SVG) | Markdown raw HTML disabled, `javascript:` links dropped, remote images not loaded, CSP `script-src 'self'`, SVG only through `<img>`, `Referrer-Policy: no-referrer`, links never navigate the panel |
| Writes outside your files | writes only under `FB_WRITABLE_ROOTS` (`$HOME:/tmp`), symlinks resolved; deletion moves to the Trash |
| The viewer window's URL (visible in its address bar) | holds a one-time code, valid 60 s, traded once for the token; never the token itself |
| A forged error toast | bridge errors arrive only through `/internal/error` with the per-launch bridge secret; the text is capped at 300 characters, control characters are dropped, and the panel shows it as plain text |
| Typing into the wrong terminal | terminal commands need a per-launch bridge secret inside fbd, the key of the pane the panel shows, an idle shell for `cd`, and no control characters |

Report a vulnerability privately through the repository's security advisories
("Report a vulnerability"), not in a public issue. Include the version (`git rev-parse
HEAD`), macOS and iTerm2 versions, and the smallest reproduction.
