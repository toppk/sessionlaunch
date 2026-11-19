"""Small synchronous client for the SessionLaunch user service."""

import json
import os
import socket

from . import __version__
from .paths import SERVICE_SOCKET
from .service_protocol import PROTOCOL_VERSION


MAX_REPLY_BYTES = 64 * 1024 * 1024


def request(action, *, path=SERVICE_SOCKET, timeout=1.5, client="cli", **fields):
    payload = (json.dumps({"action": action, **fields, "client": client,
                           "client_pid": os.getpid(), "client_version": __version__,
                           "protocol_version": PROTOCOL_VERSION}) + "\n").encode()
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
