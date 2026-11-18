#!/usr/bin/env python3
"""Headless owner of PTY history, available to the GUI and command line."""

import argparse
import json
import os
import signal
import socket
import socketserver
import stat
import subprocess
import threading
import time
from pathlib import Path

from . import inventory
from . import restore
from .sessionlaunch_paths import SERVICE_SOCKET, SESSIONS_FILE, secure_config_dir


def log(message):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | sessionlaunch.service | {message}",
          flush=True)


class InventoryState:
    def __init__(self, path=SESSIONS_FILE, interval=5.0):
        self.path = Path(path)
        self.interval = interval
        self.lock = threading.Lock()
        self.scan_lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.data = None
        self.revision = 0
        self.error = ""
        self.last_graphics_refresh = 0.0

    def scan_once(self):
        with self.scan_lock:
            if time.monotonic() - self.last_graphics_refresh >= 30:
                display = x11_environment()
                for key in ("DISPLAY", "XAUTHORITY"):
                    if display.get(key):
                        os.environ[key] = display[key]
                self.last_graphics_refresh = time.monotonic()
            data = inventory.update(self.path)
            with self.lock:
                if self.data is None or data.get("changed") or \
                        data.get("scan_status") != self.data.get("scan_status"):
                    self.data = data
                    self.revision += 1
                self.error = ""

    def poll(self):
        while not self.stop.is_set():
            try:
                first_scan = self.data is None
                started = time.monotonic()
                self.scan_once()
                if first_scan:
                    log(f"inventory ready terminals={len(self.data.get('terminal', []))} "
                        f"elapsed={time.monotonic() - started:.3f}s")
            except (OSError, ValueError, KeyError) as error:
                with self.lock:
                    if str(error) != self.error:
                        log(f"inventory scan failed: {error}")
                    self.error = str(error)
            self.wake.wait(self.interval)
            self.wake.clear()

    def snapshot(self, since=None):
        with self.lock:
            result = {"ok": True, "revision": self.revision,
                      "warming": self.data is None, "error": self.error}
            if since == self.revision and self.data is not None:
                result["unchanged"] = True
            else:
                result["data"] = self.data
            return result

    def freeze(self, key):
        with self.scan_lock:
            with self.lock:
                terminal = next((row for row in (self.data or {}).get("terminal", [])
                                 if row.get("key") == key and row.get("state") == "live"), None)
            if terminal is None:
                raise ValueError("Live terminal no longer found")
            saved = restore.freeze_terminal(terminal, self.path)
            data = inventory.update(self.path)
            with self.lock:
                self.data = data
                self.revision += 1
            return saved

    def x11_window(self, key):
        with self.lock:
            terminal = next((row for row in (self.data or {}).get("terminal", [])
                             if row.get("key") == key and row.get("state") == "live"), None)
            return terminal.get("window_id") if terminal else None


def x11_environment():
    """Read current display credentials even if the service predates login."""
    env = os.environ.copy()
    try:
        result = subprocess.run(["systemctl", "--user", "show-environment"],
                                capture_output=True, text=True, timeout=3, check=True)
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in ("DISPLAY", "XAUTHORITY"):
                env[key] = value
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return env


def dispatch(state, request):
    if not isinstance(request, dict):
        raise ValueError("Request must be an object")
    action = request.get("action")
    if action == "status":
        answer = state.snapshot(state.revision)
        return {"ok": True, "pid": os.getpid(), "revision": answer["revision"],
                "warming": answer["warming"], "error": answer["error"]}
    if action == "snapshot":
        since = request.get("since")
        if since is not None and not isinstance(since, int):
            raise ValueError("since must be a revision number")
        return state.snapshot(since)
    if action == "freeze":
        key = request.get("terminal_key")
        if not isinstance(key, str) or not key:
            raise ValueError("terminal_key is required")
        return {"ok": True, "saved": state.freeze(key)}
    if action == "raise":
        key = request.get("terminal_key")
        if not isinstance(key, str) or not key:
            raise ValueError("terminal_key is required")
        window_id = state.x11_window(key)
        if not window_id:
            raise ValueError("No X11 window is linked to that live terminal")
        subprocess.run(["wmctrl", "-ia", window_id], env=x11_environment(),
                       capture_output=True, text=True, timeout=5, check=True)
        return {"ok": True, "window_id": window_id}
    raise ValueError("Unknown action")


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(65537)
        try:
            if len(line) > 65536 or not line.endswith(b"\n"):
                raise ValueError("Request is too large or incomplete")
            answer = dispatch(self.server.state, json.loads(line))
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            answer = {"ok": False, "error": str(error)}
        self.wfile.write((json.dumps(answer) + "\n").encode())


class ServiceServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path, state):
        super().__init__(str(path), RequestHandler)
        self.state = state
        os.chmod(path, 0o600)


def serve(path=SERVICE_SOCKET, inventory_path=SESSIONS_FILE):
    path = Path(path)
    log(f"starting pid={os.getpid()} socket={path} inventory={inventory_path}")
    secure_config_dir()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_uid != os.getuid():
        raise PermissionError(f"Runtime directory is not owned by this user: {path.parent}")
    path.parent.chmod(0o700)
    if path.exists():
        if not stat.S_ISSOCK(path.stat().st_mode):
            raise FileExistsError(f"Refusing to replace non-socket path: {path}")
        with socket.socket(socket.AF_UNIX) as probe:
            probe.settimeout(0.3)
            try:
                probe.connect(str(path))
            except (ConnectionRefusedError, FileNotFoundError):
                path.unlink()
            else:
                raise RuntimeError("SessionLaunch service is already running")
    state = InventoryState(inventory_path)
    server = ServiceServer(path, state)
    log("socket ready")
    worker = threading.Thread(target=state.poll, name="inventory-poll", daemon=True)
    worker.start()
    stopped = threading.Event()

    def stop(_signal, _frame):
        stopped.set()
        state.stop.set()
        state.wake.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    server.timeout = 0.5
    try:
        while not stopped.is_set():
            server.handle_request()
    finally:
        state.stop.set()
        state.wake.set()
        server.server_close()
        worker.join(timeout=2)
        if path.exists() and stat.S_ISSOCK(path.stat().st_mode):
            path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=SERVICE_SOCKET)
    parser.add_argument("--inventory", type=Path, default=SESSIONS_FILE)
    args = parser.parse_args(argv)
    serve(args.socket, args.inventory)


if __name__ == "__main__":
    main()
