# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""An image pasted in the web app becomes a file where the pane's shell runs, and the page
pastes its path (AC-52). The bridge writes it, not fbd: fbd saves text only, and the bridge
already copies files to hosts over ssh (the helper's install). A pane on a host needs its
helper enabled: that is the user's yes to that host."""
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

from .. import agentctl

MAX_BYTES = 20 * 1024 * 1024
KEEP_DAYS = 7
SSH_TIMEOUT = 30          # seconds, plus one per 256 KB sent (20 MB: about 110 s)
REMOTE_DIR = ".cache/iterm-enhancer/paste"       # under the host's $HOME
# type -> (extension, does the data start like one)
TYPES = {
    "image/png": ("png", lambda d: d.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/jpeg": ("jpg", lambda d: d.startswith(b"\xff\xd8\xff")),
    "image/gif": ("gif", lambda d: d[:6] in (b"GIF87a", b"GIF89a")),
    "image/webp": ("webp", lambda d: d[:4] == b"RIFF" and d[8:12] == b"WEBP"),
}
NAME = re.compile(r"\d{8}-\d{6}-[0-9a-f]{8}(\.(png|jpg|gif|webp)|-[A-Za-z0-9._-]{1,80})")   # pasted image | uploaded file


class PasteError(Exception):
    """Why an image was not saved, for the page's toast."""


def checked_name(content_type, data):
    """The file name for an image of this type, or PasteError."""
    kind = TYPES.get(content_type.split(";")[0].strip().lower())
    if not kind:
        raise PasteError(f"Only PNG, JPEG, GIF and WebP images can be pasted, not {content_type or 'this kind of file'}.")
    if not data:
        raise PasteError("The pasted image is empty.")
    if len(data) > MAX_BYTES:
        raise PasteError(f"The image is larger than {MAX_BYTES // 2**20} MB.")
    ext, looks_right = kind
    if not looks_right(data):
        raise PasteError(f"The pasted file is not a {ext.upper()} image.")
    return f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{secrets.token_hex(4)}.{ext}"


def upload_name(filename, data):
    """The stored name of an uploaded file: time, random hex, then its own name in safe letters
    (it is typed into a shell and a remote shell line), so the user still recognizes it."""
    if not data:
        raise PasteError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise PasteError(f"The file is larger than {MAX_BYTES // 2**20} MB.")
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(filename or "").rsplit("/", 1)[-1]).strip("._-")[:80] or "file"
    return f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{secrets.token_hex(4)}-{base}"


def local_dir():
    return Path(tempfile.gettempdir()) / "iterm-enhancer-paste"     # $TMPDIR: per user on macOS


def save_local(name, data, folder=None):
    """Write the image on this Mac; its absolute path. Older pastes go."""
    folder = folder or local_dir()
    folder.mkdir(mode=0o700, exist_ok=True)
    st = folder.lstat()
    if not folder.is_dir() or folder.is_symlink() or st.st_uid != os.getuid():
        raise PasteError(f"{folder} is not a folder of this user's.")
    os.chmod(folder, 0o700)
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in folder.iterdir():
        try:
            if old.is_file() and old.lstat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    path = folder / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return str(path)


def remote_script(name):
    """The shell line that stores stdin as `name` on the host and prints its absolute path."""
    if not NAME.fullmatch(name):                      # it goes into a remote shell line
        raise PasteError(f"not a paste name: {name!r}")
    return (f'umask 077; d="$HOME/{REMOTE_DIR}"; mkdir -p "$d" && chmod 700 "$d" && '
            f'cat > "$d/{name}.part" && mv -f "$d/{name}.part" "$d/{name}" || {{ rm -f "$d/{name}.part"; exit 1; }}; '
            f'find "$d" -type f -mtime +{KEEP_DAYS} -exec rm -f {{}} + 2>/dev/null || true; '
            f'printf "%s" "$d/{name}"')


def save_remote(target, name, data):
    """Write the image on the host `target` (ssh arguments) names; its absolute path there."""
    try:
        path = agentctl.ssh(target, remote_script(name), stdin=data, timeout=SSH_TIMEOUT + len(data) / 262144).strip()
    except agentctl.AgentError as e:
        raise PasteError(str(e)) from e
    if not path.startswith("/") or not path.endswith("/" + name):
        raise PasteError(f"The host answered {path[:80]!r} instead of the image's path.")
    return path
