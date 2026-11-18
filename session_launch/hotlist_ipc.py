"""Small local request protocol for asking the running GUI to revive hotlist entries."""

import json
import os
import socket
from pathlib import Path


MAX_REQUEST_BYTES = 65536
MAX_BATCH = 20


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(runtime) if runtime else Path(os.environ.get(
        "XDG_CONFIG_HOME", Path.home() / ".config")) / "sessionlaunch"
    return root / "sessionlaunch" / "control.sock" if runtime else root / "control.sock"


def request_revive(rows, path=None):
    """Queue requests with the GUI. An acknowledgement does not mean they were launched."""
    entries = [{"agent": row["agent"], "id": row["id"],
                "cwd": row["repo"], "title": row.get("title", "")}
               for row in rows]
    if not entries or len(entries) > MAX_BATCH:
        raise ValueError(f"Choose between 1 and {MAX_BATCH} sessions")
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
