"""Small synchronous client for the SessionLaunch user service."""

import json
import socket

from .sessionlaunch_paths import SERVICE_SOCKET


MAX_REPLY_BYTES = 64 * 1024 * 1024


def request(action, *, path=SERVICE_SOCKET, timeout=1.5, **fields):
    payload = (json.dumps({"action": action, **fields}) + "\n").encode()
    if len(payload) > 65536:
        raise ValueError("Request is too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(payload)
        chunks = bytearray()
        while b"\n" not in chunks:
            chunk = connection.recv(65536)
            if not chunk:
                raise OSError("SessionLaunch service closed without a reply")
            chunks.extend(chunk)
            if len(chunks) > MAX_REPLY_BYTES:
                raise ValueError("SessionLaunch service reply is too large")
    result = json.loads(chunks.split(b"\n", 1)[0])
    if not result.get("ok"):
        raise ValueError(result.get("error", "SessionLaunch service rejected the request"))
    return result
