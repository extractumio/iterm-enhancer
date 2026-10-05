# Releasing

How releases are signed and published, and how the CI runner is set up. The rules for when
a release may happen are in [CLAUDE.md](../CLAUDE.md) §10; how users install is in the
[README](../README.md#install).

## Signed releases

Installs and upgrades accept a release only if its `SHA256SUMS` is signed by the
maintainer's release key (an ed25519 ssh key; `ssh-keygen -Y`, built into macOS). Its public
half is in `release-signers` and in `scripts/install.sh`, and every package carries it, so
`iterm-filebrowser upgrade` checks with the key of the build already installed. Once, in
your own terminal:

```bash
make signing-key        # ~/.config/iterm-filebrowser/release-key (FB_RELEASE_KEY); writes the public half
git add release-signers scripts/install.sh && git commit -m "Release key"
ssh-keygen -p -f ~/.config/iterm-filebrowser/release-key                 # give it a passphrase
ssh-add --apple-use-keychain ~/.config/iterm-filebrowser/release-key \
  && ssh-add -d ~/.config/iterm-filebrowser/release-key.pub                # keep it in the Keychain, not loaded
```

Then a release is one command, with no prompt: `make release TAG=vX.Y.Z` refuses unless
the tree is clean, a new version is above every `v*` tag and is made at `origin/main`, and
its release is not published yet; it installs the UI's dependencies with `npm ci
--ignore-scripts`, builds with `cargo --locked`, signs `SHA256SUMS` through ssh-agent (the
key loaded for two minutes with the Keychain's passphrase, then removed), checks the
signature, and only then tags `HEAD`, pushes the tag and publishes the GitHub release once
all its files are attached. A failure before signing leaves nothing public.

Commit release notes in `docs/releases/vX.Y.Z.md` before pushing the changes to
`origin/main`. The release command reads them before building and preserves their
Markdown in GitHub, appending installation and signing information. Resumed drafts
receive the same notes before publication. A missing notes file keeps the legacy
installation-only text; an empty or unreadable file aborts the release.
Notes must be a regular file inside the repository; file symlinks and parent links
that escape the repository are refused before reading their contents.

Who else could ship code to users: an upgrade installs only what this key signed, and the
key exists only on the maintainer's Mac (and its backups). Publishing needs write access to
the repository, which only the maintainer has; the CI runner has neither. Left: whoever
controls the maintainer's GitHub account (or its `gh` login on that Mac) can replace a
release's `install.sh`, which a *first* `curl | sh` trusts; and a program running as the
maintainer while the Keychain is unlocked can sign. The key's fingerprint is in every
release's notes for those who check a first install by hand. Keep the key and its
passphrase backed up: a new key makes existing installs refuse upgrades until they are
installed again with the one-line installer.

## CI runner

Set the runner up once on a Linux VM (as root; the token travels on stdin):

```bash
scp scripts/runner/setup.sh root@<vm>:/root/fb-runner-setup.sh
gh api -X POST repos/extractumio/iterm-extension/actions/runners/registration-token --jq .token \
  | ssh root@<vm> 'bash /root/fb-runner-setup.sh --name vm102-iterm'
```
