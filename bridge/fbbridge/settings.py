# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Apply only an explicit installation's native restoration/integration request (AC-43)."""
import asyncio
import json
import os
import pwd
import shlex

import iterm2

from . import setup_state as state

INTEGRATION = "Load Shell Integration Automatically"
PROFILE_KEYS = ["Guid", "Custom Command", "Command", INTEGRATION]
PREFERENCES = [("OpenArrangementAtStartup", False, "system window restoration"),
               ("OpenNoWindowsAtStartup", False, "system window restoration"),
               ("RunJobsInServers", True, "session restoration")]


def supported_shell(command):
    try:
        parts = shlex.split(command or "")
        shell = parts[0]
    except (ValueError, IndexError):
        return False
    flags = {"-l", "-i", "-il", "-li", "--login", "--interactive"}
    return (shell != "/bin/bash" and os.path.basename(shell).lower() in ("bash", "fish", "xonsh", "zsh")
            and all(arg in flags for arg in parts[1:]))


def supported_profile(properties, login_shell):
    kind = properties.get("Custom Command", "No")
    if kind == "SSH":
        return True
    if kind == "No":
        return supported_shell(login_shell)
    if kind in ("Yes", "Custom Shell"):
        return supported_shell(properties.get("Command"))
    return False


async def preference(conn, key):
    response = await iterm2.rpc.async_get_preference(conn, key)
    value = response.preferences_response.results[0].get_preference_result.json_value
    return json.loads(value) if value else None


async def apply(conn, directory):
    request = state.read(directory / state.REQUEST)
    request_id = request.get("id")
    if not isinstance(request_id, str) or len(request_id) != 32:
        return None
    errors = []
    try:
        result = state.read(directory / state.RESULT)
    except (ValueError, OSError) as e:
        result = {}
        errors.append(f"previous setup result ({type(e).__name__})")
    if result.get("id") != request_id:
        result = {"id": request_id, "status": "running", "operations": [], "changes": [], "errors": errors}
        state.write(directory / state.RESULT, result)
    if result.get("status") == "done":
        return state.notice(result)

    async def operation(identity, label, desired, getter, setter, default):
        op = next((o for o in result["operations"] if o["identity"] == identity), None)
        if op and op["done"]:
            return
        try:
            current = await asyncio.wait_for(getter(), 5)
            effective = default if current is None else current
            if not op and effective == desired:
                return
            if not op:
                op = {"identity": identity, "done": False}
                result["operations"].append(op)
                # The intent is durable before the write. A crash after the API accepted
                # it is recovered by the next read, without losing its change notice.
                state.write(directory / state.RESULT, result)
            if effective != desired:
                await asyncio.wait_for(setter(desired), 5)
            if label not in result["changes"]:
                result["changes"].append(label)
        except Exception as e:
            result["errors"].append(f"{label} ({type(e).__name__})")
        if op:
            op["done"] = True
        state.write(directory / state.RESULT, result)

    for key, desired, label in PREFERENCES:
        await operation(key, label, desired, lambda k=key: preference(conn, k),
                        lambda v, k=key: iterm2.preferences.async_set_preference(conn, k, v),
                        key == "RunJobsInServers")
    try:
        login = pwd.getpwuid(os.getuid()).pw_shell
        profiles = await asyncio.wait_for(iterm2.PartialProfile.async_query(conn, properties=PROFILE_KEYS), 5)
        for profile in profiles:
            if not supported_profile(profile.all_properties, login):
                continue
            async def get(p=profile):
                fresh = await iterm2.PartialProfile.async_query(conn, guids=[p.guid], properties=[INTEGRATION])
                if not fresh:
                    raise LookupError("Profile disappeared")
                return fresh[0].all_properties.get(INTEGRATION)
            await operation(f"profile:{profile.guid}", "automatic shell integration", True, get,
                            lambda v, p=profile: p._async_simple_set(INTEGRATION, v), False)
    except Exception as e:
        result["errors"].append(f"shell profiles ({type(e).__name__})")
    result["status"] = "done"
    state.write(directory / state.RESULT, result)
    return state.notice(result)


async def follow(conn, directory, post):
    """Consume new installation requests while running, including a same-build install."""
    while True:
        try:
            value = await apply(conn, directory)
            await asyncio.get_running_loop().run_in_executor(None, post, "/internal/setup", value)
        except Exception:
            # The request remains pending; parsing or disk errors are visible in the
            # installer timeout rather than being called a successful settings change.
            from .common import log
            log("iTerm2 setup unavailable; installation request remains pending")
        await asyncio.sleep(5)
