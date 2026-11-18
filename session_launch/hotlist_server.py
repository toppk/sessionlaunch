"""Qt side of the local hotlist control socket."""

import errno
import json
import os
import socket
import stat
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer
from shiboken6 import isValid

from . import agent_catalog
from . import hotlist_ipc


def validate_request(data):
    if not isinstance(data, dict) or data.get("action") != "revive":
        raise ValueError("Unknown request")
    entries = data.get("sessions")
    if not isinstance(entries, list) or not 1 <= len(entries) <= hotlist_ipc.MAX_BATCH:
        raise ValueError(f"Expected 1–{hotlist_ipc.MAX_BATCH} sessions")
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid session entry")
        agent = entry.get("agent")
        session_id = entry.get("id")
        cwd = entry.get("cwd")
        title = entry.get("title", "")
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


class HotlistServer(QObject):
    def __init__(self, on_revive, path=None, parent=None):
        super().__init__(parent)
        self.on_revive = on_revive
        self.path = Path(path or hotlist_ipc.socket_path())
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self.accept_connections)
        self.buffers = {}

    def start(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = self.path.parent.stat()
        if directory.st_uid != os.getuid():
            raise PermissionError(f"Control directory is not owned by this user: {self.path.parent}")
        self.path.parent.chmod(0o700)
        if self.server.listen(str(self.path)):
            return
        if self.path.exists() and stat.S_ISSOCK(self.path.stat().st_mode):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                code = probe.connect_ex(str(self.path))
            if code in (errno.ECONNREFUSED, errno.ENOENT):
                QLocalServer.removeServer(str(self.path))
                if self.server.listen(str(self.path)):
                    return
        raise OSError(f"Cannot listen on {self.path}: {self.server.errorString()}")

    def accept_connections(self):
        while self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            self.buffers[connection] = bytearray()
            connection.readyRead.connect(lambda conn=connection: self.read_request(conn))
            connection.disconnected.connect(lambda conn=connection: self.forget(conn))

    def forget(self, connection):
        self.buffers.pop(connection, None)
        if isValid(connection):
            connection.deleteLater()

    def read_request(self, connection):
        buffer = self.buffers.get(connection)
        if buffer is None:
            return
        buffer.extend(bytes(connection.readAll()))
        if len(buffer) > hotlist_ipc.MAX_REQUEST_BYTES:
            self.reply(connection, {"ok": False, "error": "Request is too large"})
            return
        if b"\n" not in buffer:
            return
        try:
            data = json.loads(buffer.split(b"\n", 1)[0])
            rows = validate_request(data)
        except (ValueError, UnicodeDecodeError) as error:
            self.reply(connection, {"ok": False, "error": str(error)})
            return
        self.reply(connection, {"ok": True, "queued": len(rows)})
        QTimer.singleShot(0, lambda: self.on_revive(rows))

    def reply(self, connection, response):
        self.buffers.pop(connection, None)
        connection.write((json.dumps(response) + "\n").encode())
        connection.flush()
        connection.disconnectFromServer()

    def stop(self):
        self.server.close()
