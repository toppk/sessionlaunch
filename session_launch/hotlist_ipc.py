"""Validate and queue hotlist revival requests without launching agents."""

import json
import os
import socket
from pathlib import Path


MAX_REQUEST_BYTES = 65536
MAX_BATCH = 20


def validate_entries(entries):
    """Return deduplicated requests suitable for the GUI confirmation queue."""
    from . import agent_catalog

    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BATCH:
        raise ValueError(f"Expected 1–{MAX_BATCH} sessions")
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid session entry")
        agent, session_id = entry.get("agent"), entry.get("id")
        cwd, title = entry.get("cwd"), entry.get("title", "")
        if (agent not in ("claude", "codex") or not isinstance(session_id, str)
                or not agent_catalog.SESSION_ID.fullmatch(session_id)
                or not isinstance(cwd, str) or not Path(cwd).is_absolute()
                or not isinstance(title, str) or len(title) > 200):
            raise ValueError("Invalid agent, session ID, directory, or title")
        key = (agent, session_id.lower())
        if key not in seen:
            result.append({"agent": agent, "id": session_id.lower(),
                           "cwd": cwd, "title": title})
            seen.add(key)
    return result


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(runtime) if runtime else Path(os.environ.get(
        "XDG_CONFIG_HOME", Path.home() / ".config")) / "sessionlaunch"
    return root / "sessionlaunch" / "control.sock" if runtime else root / "control.sock"


def request_revive(rows, path=None):
    """Queue requests with the service. An acknowledgement is never a launch."""
    entries = [{"agent": row["agent"], "id": row["id"],
                "cwd": row["repo"], "title": row.get("title", "")}
               for row in rows]
    entries = validate_entries(entries)
    if path is None:
        from . import sessionlaunch_service_client

        return sessionlaunch_service_client.request(
            "revive_queue", sessions=entries, client="cli")["queued"]
    payload = (json.dumps({"action": "revive", "sessions": entries}) + "\n").encode()
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("Request is too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path or socket_path()))
        connection.sendall(payload)
        answer = bytearray()
        while b"\n" not in answer and len(answer) < 4096:
            chunk = connection.recv(4096)
            if not chunk:
                break
            answer.extend(chunk)
    if b"\n" not in answer:
        raise OSError("SessionLaunch did not acknowledge the request")
    response = json.loads(answer.split(b"\n", 1)[0])
    if not response.get("ok"):
        raise ValueError(response.get("error", "SessionLaunch rejected the request"))
    return int(response["queued"])
