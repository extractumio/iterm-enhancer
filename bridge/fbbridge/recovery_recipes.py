# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Narrow SSH recipes and controlled launch properties; never replay job argv."""
import os
import re
import shlex

import iterm2

from .common import UserError

DESTINATION = re.compile(r"^[A-Za-z0-9@._:\[\]-]+$")
WITH_VALUE = {"-p", "-l", "-i", "-J", "-F"}
FLAGS = {"-4", "-6", "-C"}


def ssh_recipe(argv):
    if not argv or os.path.basename(argv[0]) != "ssh":
        return None
    args, i = [], 1
    while i < len(argv):
        arg = argv[i]
        if not isinstance(arg, str) or any(ord(c) < 32 or ord(c) == 127 for c in arg):
            return None
        if arg == "--":
            i += 1
            break
        if not arg.startswith("-"):
            break
        if arg in FLAGS:
            args.append(arg)
        elif arg in ("-t", "-tt", "-T", "-q", "-v", "-vv", "-vvv"):
            pass  # recovery controls its own interactive TTY and verbosity
        else:
            option = arg[:2]
            if option not in WITH_VALUE:
                return None  # includes executable -o options and forwarding commands
            value = arg[2:]
            if not value:
                i += 1
                if i >= len(argv):
                    return None
                value = argv[i]
            if not value or value.startswith("-") or any(ord(c) < 32 or ord(c) == 127 for c in value):
                return None
            if option == "-p" and (not value.isdigit() or not 0 < int(value) < 65536):
                return None
            if option in ("-l", "-J") and not re.fullmatch(r"[A-Za-z0-9@._:\[\],-]+", value):
                return None
            args.extend([option, value])
        i += 1
    if i >= len(argv) or not DESTINATION.fullmatch(argv[i]) or argv[i].startswith("-"):
        return None
    # No arbitrary remote command, including shell wrappers or an application.
    if i + 1 != len(argv):
        return None
    return args + [argv[i]]


def remote_bootstrap(cwd):
    if cwd is None:
        return None
    if not cwd.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in cwd):
        raise UserError("Unsafe remote directory")
    return directory_bootstrap(cwd, "/bin/sh -l")


def capture_ssh_recipe(argv):
    """Round-trip only our exact generated launch; never retain or replay its command."""
    direct = ssh_recipe(argv)
    if direct or not argv:
        return direct
    if argv[1:6] != ["-tt", "-o", "RemoteCommand=none", "-o", "PermitLocalCommand=no"]:
        return None
    try:
        end = argv.index("--", 6) + 2
        recipe = ssh_recipe([argv[0], *argv[6:end]])
        if not recipe or len(argv) not in (end, end + 1):
            return None
        if len(argv) == end:
            return recipe
        command = shlex.split(argv[end])
        if len(command) != 3 or command[:2] != ["/bin/sh", "-c"]:
            return None
        script = command[2]
        words = shlex.split(script)
        if words[:4] == ["if", "!", "cd", "--"]:
            cwd = words[4].removesuffix(";")
            expected = remote_bootstrap(cwd)
        elif words[:2] == ["cd", "--"]:
            cwd = words[2]
            remote_bootstrap(cwd)  # Validate the pre-fallback format on upgrade too.
            expected = f"cd -- {shlex.quote(cwd)} || exit 1; exec /bin/sh -l"
        else:
            return None
        return recipe if script == expected else None
    except (ValueError, IndexError, UserError):
        return None


def directory_bootstrap(cwd, command):
    """The cwd may disappear after preflight; retain an interactive shell and diagnostic."""
    return (f"if ! cd -- {shlex.quote(cwd)}; then "
            "printf '%s\\n' 'iterm-enhancer: recorded directory unavailable; opening shell in home or filesystem root' >&2; "
            'cd -- "$HOME" || cd / || exit 1; fi; exec ' + command)


def local_shell(command, cwd):
    return shlex.join(["/bin/sh", "-c", directory_bootstrap(cwd, command)]) if cwd else command


def directory_available(cwd):
    return bool(cwd) and os.path.isdir(cwd) and os.access(cwd, os.X_OK)


def fallback_directory():
    home = os.path.expanduser("~")
    return os.path.realpath(home) if directory_available(home) else "/"


def ssh_command(args, cwd=None, command=None):
    if ssh_recipe(["ssh", *args]) != args:
        raise UserError("Unsupported SSH recovery recipe")
    remote = command if command is not None else remote_bootstrap(cwd)
    argv = ["ssh", "-tt", "-o", "RemoteCommand=none", "-o", "PermitLocalCommand=no", *args[:-1], "--", args[-1]]
    if remote:
        # SSH evaluates the command in the account's shell, which may be fish.
        # Only a simple explicit sh invocation crosses that shell boundary.
        argv.append(shlex.join(["/bin/sh", "-c", remote]))
    return shlex.join(argv)


def launch_profile(marker, command, cwd=None, close_on_end=True):
    p = iterm2.LocalWriteOnlyProfile()
    values = {"Custom Command": "Yes", "Command": command, "Initial Text": "", "Name": marker,
              "Run Command In Login Shell": False, "Triggers": [], "Close Sessions On End": close_on_end,
              "Custom Directory": "Yes", "Working Directory": cwd or fallback_directory()}
    for key, value in values.items():
        p._simple_set(key, value)
    return p
