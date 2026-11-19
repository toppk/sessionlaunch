#!/usr/bin/env python3
"""Headless owner of PTY history, available to the GUI and command line."""

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import socketserver
import stat
import subprocess
import threading
import time
from pathlib import Path

from . import __version__, agent_catalog, agent_link, claude_peers, inventory, manual_links, repo_tags, tag_migration
from . import restore
from .session_identity import agent_session_key
from .session_identity import terminal_aliases, terminal_session_key
from .paths import SERVICE_SOCKET, SESSIONS_FILE, private_write_text, secure_config_dir
from .session_groups import SCHEMA_VERSION, SessionGroups
from .service_protocol import PROTOCOL_VERSION


WINDOW_ID = re.compile(r"0x[0-9a-fA-F]+\Z")


def log(message):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | sessionlaunch.service | {message}",
          flush=True)


class InventoryState:
    def __init__(self, path=SESSIONS_FILE, interval=5.0):
        self.path = Path(path)
        self.groups_path = self.path.parent / "groups.json"
        self.links_path = self.path.parent / "manual_links.json"
        self.settings_path = self.path.parent / "settings.toml"
        self.tags_path = self.path.parent / "tags.json"
        self.rcfile_path = self.path.parent / "startup.bashrc"
        self.launches = {}
        self.launch_lock = threading.Lock()
        self.groups_lock = threading.Lock()
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
                for key in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY",
                            "DBUS_SESSION_BUS_ADDRESS", "PATH"):
                    if display.get(key):
                        os.environ[key] = display[key]
                self.last_graphics_refresh = time.monotonic()
            data = inventory.update(self.path)
            with self.groups_lock:
                moved = SessionGroups(self.groups_path).migrate_aliases(
                    terminal_aliases(data.get("terminal", [])))
            if moved:
                log(f"migrated {moved} terminal organization aliases to session IDs")
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

    def organization(self, since=None):
        with self.groups_lock:
            if self.groups_path.exists():
                SessionGroups(self.groups_path)  # Validate and migrate on the owner.
                raw = self.groups_path.read_bytes()
                document = json.loads(raw)
            else:
                document = {"schema_version": SCHEMA_VERSION, "groups": [],
                            "memberships": {}}
                raw = b""
            revision = hashlib.sha256(raw).hexdigest()
            result = {"ok": True, "revision": revision}
            if since == revision:
                result["unchanged"] = True
            else:
                result["data"] = document
            return result

    def save_organization(self, document, expected_revision):
        if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported organization document")
        if not isinstance(expected_revision, str):
            raise ValueError("Organization revision is required")
        with self.groups_lock:
            raw = self.groups_path.read_bytes() if self.groups_path.exists() else b""
            if hashlib.sha256(raw).hexdigest() != expected_revision:
                raise ValueError("Organization changed in another client; reload and retry")
            SessionGroups(self.groups_path, data=document, migrate=False)
            private_write_text(self.groups_path, json.dumps(document, indent=2) + "\n")
            revision = hashlib.sha256(self.groups_path.read_bytes()).hexdigest()
            return {"ok": True, "revision": revision}

    def change_manual_link(self, request, unlink=False):
        key = request.get("terminal_key")
        if not isinstance(key, str) or not key:
            raise ValueError("Terminal key is required")
        if not unlink:
            provider, session_id, cwd = (request.get("provider"),
                                         request.get("session_id"), request.get("cwd"))
            if not agent_session_key(provider, session_id) or not isinstance(cwd, str):
                raise ValueError("Invalid manual agent link")
            agent_link.verify_session(provider, session_id, cwd)
        with self.scan_lock, self.groups_lock:
            fresh = inventory.update(self.path)
            with self.lock:
                self.data = fresh
                self.revision += 1
                terminal = next((row for row in fresh.get("terminal", [])
                                 if row.get("key") == key and row.get("state") == "live"), None)
            if not terminal:
                raise ValueError("Live terminal no longer found")
            links = manual_links.load(self.links_path)
            alias = f"terminal:{key}"
            groups = SessionGroups(self.groups_path)
            if unlink:
                link = links.get(key)
                if not link or not manual_links.matches(terminal, link):
                    raise ValueError("Manual link no longer matches this process")
                canonical = agent_session_key(link["provider"], link["session_id"])
                links.pop(key)
                manual_links.save(links, self.links_path)
                try:
                    groups.unlink_terminal(alias, canonical, link.get("moved_metadata", False))
                except (OSError, ValueError):
                    links[key] = link
                    manual_links.save(links, self.links_path)
                    raise
            else:
                provider = request.get("provider")
                session_id = request.get("session_id")
                canonical = agent_session_key(provider, session_id)
                if not canonical or terminal.get("agent") != provider:
                    raise ValueError("Agent provider or session ID does not match terminal")
                if (terminal.get("agent_pid") != request.get("agent_pid") or
                        terminal.get("agent_start_ticks") != request.get("agent_start_ticks") or
                        (terminal.get("agent_cwd") or terminal.get("cwd")) != request.get("cwd")):
                    raise ValueError("Agent process changed during linking")
                if any(other.get("key") != key and other.get("state") == "live" and
                       other.get("agent") == provider and
                       other.get("session_id") == session_id
                       for other in fresh.get("terminal", [])):
                    raise ValueError("Session UUID is already attached to another terminal")
                if key in links and manual_links.matches(terminal, links[key]):
                    raise ValueError("Terminal already has a manual link")
                moved = groups.link_terminal(alias, canonical)
                links[key] = {"terminal_key": key, "provider": provider,
                              "session_id": session_id,
                              "agent_pid": request["agent_pid"],
                              "agent_start_ticks": request["agent_start_ticks"],
                              "cwd": request["cwd"], "moved_metadata": moved}
                try:
                    manual_links.save(links, self.links_path)
                except OSError:
                    groups.unlink_terminal(alias, canonical, moved)
                    raise
            data = inventory.update(self.path)
            with self.lock:
                self.data = data
                self.revision += 1
            return {"ok": True, "revision": self.revision}

    def legacy_tags(self):
        if not self.tags_path.exists():
            return {}
        data = json.loads(self.tags_path.read_text())
        if not isinstance(data, dict):
            raise ValueError("Invalid legacy tag file")
        return data

    def migrate_legacy_tags(self, plan, identities):
        if not isinstance(plan, dict) or not isinstance(identities, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in identities.items()):
            raise ValueError("Invalid tag migration request")
        with self.groups_lock:
            groups = SessionGroups(self.groups_path)
            tags = self.legacy_tags()
            if set(identities) != set(tags):
                raise ValueError("Tag identities changed; reload before migrating")
            tag_migration.apply_migration(groups, tags, plan,
                                          identities.__getitem__, self.tags_path)
            return {"ok": True}

    def launch_terminal(self, request):
        name = request.get("terminal")
        directory = request.get("directory")
        title = request.get("title")
        command = request.get("command")
        identity = request.get("identity", "")
        frozen_id = request.get("frozen_id", "")
        shell = request.get("shell", "")
        if name not in restore.TERMINALS or not all(
                isinstance(value, str) for value in
                (directory, title, command, identity, frozen_id, shell)) or \
                not directory or not command or len(command) > 65536 or len(title) > 200:
            raise ValueError("Invalid terminal launch request")
        if len(identity) > 200:
            raise ValueError("Invalid launch identity")
        with self.scan_lock, self.launch_lock:
            fresh = inventory.update(self.path)
            with self.lock:
                self.data = fresh
                self.revision += 1
            if identity and any(row.get("state") == "live" and
                                terminal_session_key(row) == identity
                                for row in fresh.get("terminal", [])):
                raise ValueError("Session is already running")
            if identity.startswith("agent:"):
                parts = identity.split(":", 2)
                if len(parts) != 3 or agent_session_key(parts[1], parts[2]) != identity:
                    raise ValueError("Invalid agent launch identity")
                if parts[1] == "claude" and claude_peers.peer_for(
                        parts[2], claude_peers.read_peers()):
                    raise ValueError("Claude session is already running as a peer")
                if unlinked := agent_catalog.unlinked_running_terminal(
                        {"agent": parts[1], "cwd": directory}, fresh.get("terminal", [])):
                    raise ValueError(f"Agent may already be running unlinked in "
                                     f"{unlinked.get('pty') or 'another terminal'}; link it first")
            if identity and any(entry["identity"] == identity and
                                entry["handle"].poll() is None
                                for entry in self.launches.values()):
                raise ValueError("Session is already starting")
            graphical = x11_environment()
            for key in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY",
                        "DBUS_SESSION_BUS_ADDRESS", "PATH"):
                if value := graphical.get(key):
                    os.environ[key] = value
            restore.install_rcfile(self.rcfile_path)
            handle = restore.launch(name, directory, title, command, self.rcfile_path,
                                    frozen_id, shell,
                                    restore.read_settings(self.settings_path)["launch_backend"])
            launch_id = f"launch-{os.urandom(16).hex()}"
            self.launches[launch_id] = {"handle": handle, "identity": identity}
            return {"ok": True, "launch_id": launch_id,
                    "unit": getattr(handle, "unit", "")}

    def launch_status(self, launch_id):
        if not isinstance(launch_id, str):
            raise ValueError("Launch ID is required")
        with self.launch_lock:
            entry = self.launches.get(launch_id)
            if entry is None:
                return {"ok": True, "exit_code": 1}
            return {"ok": True, "exit_code": entry["handle"].poll()}


def x11_environment():
    """Read current display credentials even if the service predates login."""
    env = os.environ.copy()
    try:
        result = subprocess.run(["systemctl", "--user", "show-environment"],
                                capture_output=True, text=True, timeout=3, check=True)
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY",
                                     "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "PATH"):
                env[key] = value
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return env


def listed_windows():
    result = subprocess.run(["wmctrl", "-lxp"], env=x11_environment(),
                            capture_output=True, text=True, timeout=3, check=True)
    return result.stdout.splitlines()


def window_probe(window_ids):
    """Gather X11 failure evidence on the process that owns window inspection."""
    if not isinstance(window_ids, list) or len(window_ids) > 20 or not all(
            isinstance(value, str) and re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)", value)
            for value in window_ids):
        raise ValueError("Invalid window IDs")
    env = x11_environment()
    try:
        result = subprocess.run(["wmctrl", "-lxp"], env=env, capture_output=True,
                                text=True, timeout=3, check=False)
        answer = {"returncode": result.returncode, "lines": result.stdout.splitlines(),
                  "stderr": result.stderr.strip(), "properties": {}}
    except (OSError, subprocess.TimeoutExpired) as error:
        answer = {"returncode": None, "lines": [], "stderr": str(error),
                  "properties": {}}
    for value in window_ids:
        try:
            result = subprocess.run(
                ["xprop", "-id", value, "_NET_WM_PID", "WM_CLASS", "WM_NAME"],
                env=env, capture_output=True, text=True, timeout=2, check=False)
            answer["properties"][value] = {"returncode": result.returncode,
                                           "stdout": result.stdout.strip(),
                                           "stderr": result.stderr.strip()}
        except (OSError, subprocess.TimeoutExpired) as error:
            answer["properties"][value] = {"error": str(error)}
    return answer


def window_from_token(token):
    if not isinstance(token, str) or not token.startswith("sessionlaunch-") or len(token) > 80:
        raise ValueError("Invalid SessionLaunch window token")
    for line in listed_windows():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and (token in fields[3] or token in fields[5]):
            return fields[0]
    return None


def act_x11_window(window_id, action):
    if not isinstance(window_id, str) or not WINDOW_ID.fullmatch(window_id):
        raise ValueError("Invalid X11 window ID")
    if action not in ("raise", "lower", "minimize", "maximize"):
        raise ValueError("Unknown window action")
    if action == "raise":
        command = ["wmctrl", "-ia", window_id]
    elif action == "maximize":
        command = ["wmctrl", "-ir", window_id, "-b",
                   "toggle,maximized_vert,maximized_horz"]
    else:
        from Xlib import X, display

        for key, value in x11_environment().items():
            if key in ("DISPLAY", "XAUTHORITY"):
                os.environ[key] = value
        try:
            connection = display.Display()
            try:
                window = connection.create_resource_object("window", int(window_id, 16))
                if action == "lower":
                    window.configure(stack_mode=X.Below)
                else:
                    window.iconify(connection.get_default_screen())
                connection.sync()
            finally:
                connection.close()
        except Exception as error:
            raise ValueError(f"X11 {action} failed: {error}") from error
        return
    subprocess.run(command, env=x11_environment(), capture_output=True,
                   text=True, timeout=5, check=True)


def dispatch(state, request):
    if not isinstance(request, dict):
        raise ValueError("Request must be an object")
    action = request.get("action")
    if action == "status":
        answer = state.snapshot(state.revision)
        return {"ok": True, "pid": os.getpid(), "version": __version__,
                "revision": answer["revision"],
                "warming": answer["warming"], "error": answer["error"]}
    if action == "snapshot":
        since = request.get("since")
        if since is not None and not isinstance(since, int):
            raise ValueError("since must be a revision number")
        return state.snapshot(since)
    if action == "organization_get":
        since = request.get("since")
        if since is not None and not isinstance(since, str):
            raise ValueError("Organization revision must be a string")
        return state.organization(since)
    if action == "organization_put":
        return state.save_organization(request.get("data"), request.get("revision"))
    if action == "manual_link":
        return state.change_manual_link(request)
    if action == "manual_unlink":
        return state.change_manual_link(request, unlink=True)
    if action == "settings_get":
        return {"ok": True, "settings": restore.read_settings(state.settings_path)}
    if action == "settings_set_terminal":
        restore.write_settings(state.settings_path, request.get("terminal"))
        return {"ok": True, "settings": restore.read_settings(state.settings_path)}
    if action == "legacy_tags_get":
        return {"ok": True, "tags": state.legacy_tags()}
    if action == "legacy_tags_migrate":
        return state.migrate_legacy_tags(request.get("plan"), request.get("identities"))
    if action == "launch_terminal":
        return state.launch_terminal(request)
    if action == "launch_status":
        return state.launch_status(request.get("launch_id"))
    if action == "window_probe":
        return {"ok": True, "probe": window_probe(request.get("window_ids"))}
    if action == "claude_peers":
        known = request.get("known_pids", [])
        if not isinstance(known, list) or len(known) > 1000 or not all(
                isinstance(pid, int) and pid > 0 for pid in known):
            raise ValueError("Invalid known peer PIDs")
        dead = []
        for pid in known:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                dead.append(pid)
            except PermissionError:
                pass
        return {"ok": True, "peers": claude_peers.read_peers(), "dead_pids": dead}
    if action == "repo_tag":
        path = request.get("repo_path")
        if not isinstance(path, str) or len(path) > 8192:
            raise ValueError("Invalid repository path")
        return {"ok": True, "tag": repo_tags.repo_tag_for(path)}
    if action == "agent_query":
        mode = request.get("mode")
        session_id = request.get("session_id")
        if mode == "list":
            argv = ["session", "list", "--json", "--limit", "500", "--sort", "recent"]
        elif mode in ("get", "messages") and isinstance(session_id, str) and \
                0 < len(session_id) <= 128:
            argv = (["session", "get", session_id, "--json"] if mode == "get" else
                    ["session", "messages", session_id, "--direction", "desc",
                     "--limit", "8", "--role", "user,assistant", "--json"])
        elif mode == "candidates":
            provider, cwd = request.get("provider"), request.get("cwd")
            if provider not in ("claude", "codex") or not isinstance(cwd, str) or \
                    not cwd or len(cwd) > 8192:
                raise ValueError("Invalid agent candidate request")
            return {"ok": True, "data": agent_link.candidates(provider, cwd)}
        else:
            raise ValueError("Unknown agent query")
        result = subprocess.run(["agentsview", *argv], capture_output=True,
                                text=True, timeout=20, check=False)
        if result.returncode:
            raise ValueError(result.stderr.strip() or
                             f"AgentsView exited {result.returncode}")
        return {"ok": True, "data": json.loads(result.stdout)}
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
        act_x11_window(window_id, "raise")
        return {"ok": True, "window_id": window_id}
    if action == "window_find":
        return {"ok": True, "window_id": window_from_token(request.get("token"))}
    if action == "window_action":
        act_x11_window(request.get("window_id"), request.get("operation"))
        return {"ok": True}
    raise ValueError("Unknown action")


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(65537)
        try:
            if len(line) > 65536 or not line.endswith(b"\n"):
                raise ValueError("Request is too large or incomplete")
            request = json.loads(line)
            self.server.note_client(request)
            answer = dispatch(self.server.state, request)
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            answer = {"ok": False, "error": str(error)}
        answer.update({"service_version": __version__,
                       "protocol_version": PROTOCOL_VERSION, "service_pid": os.getpid()})
        self.wfile.write((json.dumps(answer) + "\n").encode())


class ServiceServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path, state):
        super().__init__(str(path), RequestHandler)
        self.state = state
        self.client_lock = threading.Lock()
        self.seen_clients = set()
        os.chmod(path, 0o600)

    def note_client(self, request):
        if not isinstance(request, dict):
            return
        client = request.get("client")
        version = request.get("client_version")
        protocol = request.get("protocol_version")
        pid = request.get("client_pid")
        if (not isinstance(client, str) or not isinstance(version, str) or
                not isinstance(protocol, int) or not isinstance(pid, int)):
            return
        key = (client, pid if client == "gui" else None, version, protocol)
        with self.client_lock:
            if key in self.seen_clients:
                return
            self.seen_clients.add(key)
        log(f"client connected kind={client} pid={pid} version={version} "
            f"protocol={protocol} service_version={__version__} "
            f"service_protocol={PROTOCOL_VERSION}")


def serve(path=SERVICE_SOCKET, inventory_path=SESSIONS_FILE):
    path = Path(path)
    log(f"starting version={__version__} pid={os.getpid()} socket={path} "
        f"inventory={inventory_path}")
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
