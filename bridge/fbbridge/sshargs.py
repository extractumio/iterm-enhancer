# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The ssh destination of a remote tmux -CC session (AC-38): from the arguments of the `ssh`
that iTerm2's tmux gateway runs, keep what says where and how to connect (destination,
port, user, identity, jump hosts, config file, `-o` options) and drop what that session
did with the connection (a TTY, forwardings, its remote command), so the bridge's own
ssh reaches the same host the same way."""

# options of ssh(1) that take a value
WITH_VALUE = set("BbcDEeFIiJLlmOoPpQRSWw")
# of those, the ones that decide where and how to connect
KEEP_VALUE = set("FiJlopPcS")
KEEP_FLAGS = set("46C")
# -o keys kept (an allowlist: anything else may belong to that session, not the connection)
KEEP_O = {"port", "user", "hostname", "identityfile", "identitiesonly", "identityagent", "certificatefile",
          "proxyjump", "proxycommand", "controlpath", "controlmaster", "controlpersist", "hostkeyalias",
          "stricthostkeychecking", "userknownhostsfile", "addressfamily", "connecttimeout",
          "serveraliveinterval", "serveralivecountmax", "pubkeyauthentication", "preferredauthentications",
          "kexalgorithms", "hostkeyalgorithms", "ciphers", "macs", "compression"}


def key(target):
    """A host's name in the record and in requests: its ssh arguments, as typed."""
    return " ".join(target)


def target(argv):
    """The ssh arguments that reach the same host (options + destination), or None when
    `argv` is not an ssh command line with a destination."""
    if not argv or argv[0].rsplit("/", 1)[-1] != "ssh" or not all(a.isascii() for a in argv):
        return None  # non-ASCII arguments could not travel in a request header
    keep, i = [], 1
    while i < len(argv):
        a = argv[i]
        if a == "--":
            return keep + [argv[i + 1]] if i + 1 < len(argv) else None
        if not a.startswith("-") or a == "-":
            return keep + [a]                           # the destination; the rest is its command
        j = 1
        while j < len(a):
            c = a[j]
            if c in WITH_VALUE:
                value = a[j + 1:] or (argv[i + 1] if i + 1 < len(argv) else None)
                if value is None:
                    return None
                if not a[j + 1:]:
                    i += 1
                if c in KEEP_VALUE and (c != "o" or (value.replace("=", " ").split() or [""])[0].lower() in KEEP_O):
                    keep += [f"-{c}", value]
                break
            if c in KEEP_FLAGS:
                keep.append(f"-{c}")
            j += 1
        i += 1
    return None
