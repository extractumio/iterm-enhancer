# Contributing

Thanks for helping. Read [CLAUDE.md](CLAUDE.md) first: it holds the project rules (English
only, the 500-line cap, tests and the Definition of Done) for humans and AI agents alike.

## Contributor license agreement

The project is dual-licensed (AGPL-3.0 and a commercial license, see
[LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md)). To keep that possible, every contribution
needs this agreement. By opening a pull request you confirm that:

1. You wrote the contribution, or have the right to submit it; if your employer has rights
   in it, your employer has allowed you to contribute it under these terms.
2. You grant Gregory Zemskov and any successors and assigns ("the Maintainer") a
   perpetual, worldwide, non-exclusive, royalty-free, irrevocable license to use, modify,
   sublicense and relicense the contribution, including under the commercial license, and
   to transfer these rights.
3. You grant the Maintainer a patent license for any of your patents that the contribution
   necessarily infringes, on the same terms.
4. You keep the copyright of your contribution and may use it under any terms.

The agreement is recorded by a CLA check on the pull request (until it is set up:
`Signed-off-by: Your Name <you@example.com>` on each commit, `git commit -s`).

## Before a pull request

```bash
make test                         # cargo test, typecheck, UI unit tests
cd ui && node test/e2e_panel.mjs  # browser checks (once: npx playwright-core install chromium-headless-shell)
```

Changes to behavior update `docs/specs/iterm-file-browser.md` (scenarios first) in the same
pull request.
