#!/usr/bin/env python3
"""Read live Linux PTYs and preserve observations in the SessionLaunch config dir.

This module only inspects processes. It never starts a terminal or an agent.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from . import agent_identity
from . import manual_links
from .sessionlaunch_paths import (LEGACY_SESSIONS_FILE, SESSIONS_FILE,
                                 private_write_text, secure_config_dir)


ROOT = Path(__file__).resolve().parent
AGENTS = {"codex", "claude"}
TERMINALS = {
    "xterm", "xterm+", "revenant", "ghostty", "kitty", "alacritty", "wezterm-gui",
    "foot", "konsole", "gnome-terminal-server", "ptyxis-agent", "tilix",
    "xfce4-terminal", "lxterminal", "terminator", "st", "urxvt", "rio",
}
SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
RETENTION = timedelta(days=90)
SHELLS = {"bash", "dash", "fish", "sh", "zsh", "ksh", "tcsh", "csh"}
OWNER_SWEEP_INTERVAL = 30
_owner_scan = {"ptys": None, "at": 0.0, "pids": set()}
_previous_cache = {}


def toml_string(value):
    return json.dumps(str(value), ensure_ascii=False)


def process_info(pid, proc_root=Path("/proc")):
    directory = proc_root / str(pid)
    try:
        stat = (directory / "stat").read_text()
        # The comm field is parenthesized and can contain spaces or ')' characters.
        close = stat.rfind(") ")
        comm = stat[stat.find("(") + 1:close]
        fields = stat[close + 2:].split()
        tty_dev = int(fields[4])
        pty = ""
        if tty_dev and 136 <= os.major(tty_dev) <= 143:
            pty = f"/dev/pts/{os.minor(tty_dev)}"
        if not pty:
            for fd in ("0", "1", "2"):
                try:
                    target = os.readlink(directory / "fd" / fd)
                    if re.fullmatch(r"/dev/pts/\d+", target):
                        pty = target
                        break
                except OSError:
                    pass
        try:
            cwd = os.readlink(directory / "cwd")
        except OSError:
            cwd = ""
        try:
            argv = (directory / "cmdline").read_bytes().strip(b"\0").decode(
                "utf-8", "replace").split("\0")
        except OSError:
            argv = []
        try:
            environment = (directory / "environ").read_bytes().split(b"\0")
            pwd_env = next((entry[4:].decode("utf-8", "replace") for entry in environment
                            if entry.startswith(b"PWD=")), "")
            frozen_id = next((entry[24:].decode("utf-8", "replace") for entry in environment
                              if entry.startswith(b"SESSIONLAUNCH_FROZEN_ID=")), "")
            launch_unit = next((entry[19:].decode("utf-8", "replace") for entry in environment
                                if entry.startswith(b"SESSIONLAUNCH_UNIT=")), "")
            window_id_env = next((entry[9:].decode("utf-8", "replace") for entry in environment
                                  if entry.startswith(b"WINDOWID=")), "")
        except OSError:
            pwd_env = ""
            frozen_id = ""
            launch_unit = ""
            window_id_env = ""
        try:
            status = (directory / "status").read_text()
            uids = re.search(r"^Uid:\s+(\d+)\s+(\d+)", status, re.M)
            uid, euid = (int(uids[1]), int(uids[2])) if uids else (-1, -1)
        except OSError:
            uid, euid = -1, -1
        return {
            "pid": pid, "ppid": int(fields[1]), "pgid": int(fields[2]),
            "sid": int(fields[3]), "tpgid": int(fields[5]), "pty": pty,
            "comm": comm, "cwd": cwd, "pwd_env": pwd_env,
            "uid": uid, "euid": euid,
            "argv": argv, "frozen_id": frozen_id, "launch_unit": launch_unit,
            "window_id_env": window_id_env,
            "start_ticks": int(fields[19]),
        }
    except (OSError, ValueError, IndexError):
        return None


def process_brief(pid, proc_root=Path("/proc")):
    """Read only enough of /proc to find processes attached to a PTY."""
    directory = f"{os.fspath(proc_root)}/{pid}"
    try:
        with open(f"{directory}/stat") as stream:
            stat = stream.read()
        close = stat.rfind(") ")
        comm = stat[stat.find("(") + 1:close]
        fields = stat[close + 2:].split()
        tty_dev = int(fields[4])
        pty = (f"/dev/pts/{os.minor(tty_dev)}"
               if tty_dev and 136 <= os.major(tty_dev) <= 143 else "")
        if not pty:
            for fd in ("0", "1", "2"):
                try:
                    target = os.readlink(f"{directory}/fd/{fd}")
                    if re.fullmatch(r"/dev/pts/\d+", target):
                        pty = target
                        break
                except OSError:
                    pass
        return {"pid": pid, "ppid": int(fields[1]), "comm": comm, "pty": pty}
    except (OSError, ValueError, IndexError):
        return None


def master_ptys(pid, proc_root=Path("/proc")):
    """Find slave PTYs held open through this process's /dev/ptmx FDs."""
    directory = f"{os.fspath(proc_root)}/{pid}"
    try:
        fds = os.listdir(f"{directory}/fdinfo")
    except OSError:
        return set()
    result = set()
    for fd in fds:
        try:
            target = os.readlink(f"{directory}/fd/{fd}")
            if target not in ("/dev/ptmx", "/dev/pts/ptmx"):
                continue
            with open(f"{directory}/fdinfo/{fd}") as stream:
                match = re.search(r"^tty-index:\s*(\d+)$", stream.read(), re.M)
            if match:
                result.add(f"/dev/pts/{match.group(1)}")
        except OSError:
            continue
    return result


def agent_for(process):
    comm = process["comm"].casefold()
    argv = process["argv"]
    executable = Path(argv[0]).name.casefold() if argv else ""
    if comm in AGENTS:
        return comm
    if executable in AGENTS:
        return executable
    if executable in ("node", "nodejs") and len(argv) > 1:
        script = argv[1].casefold()
        if "claude-code" in script and script.endswith("cli.js"):
            return "claude"
    return ""


def windows_by_pid():
    try:
        result = subprocess.run(["wmctrl", "-lxp"], capture_output=True,
                                text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    windows = {}
    if result.returncode:
        return windows
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6:
            continue
        try:
            pid = int(fields[2])
        except ValueError:
            continue
        windows.setdefault(pid, []).append({"id": fields[0], "class": fields[3],
                                             "title": fields[5]})
    return windows


def window_from_environment(attached, owner_pid):
    """Use the slave-side WINDOWID only when X11 confirms the PTY master's PID."""
    values = {process.get("window_id_env", "") for process in attached}
    values.discard("")
    if len(values) != 1 or not owner_pid:
        return {}
    value = values.pop()
    try:
        window_id = int(value, 16 if value.lower().startswith("0x") else 10)
        result = subprocess.run(["xprop", "-id", str(window_id), "_NET_WM_PID", "WM_CLASS"],
                                capture_output=True, text=True, timeout=2, check=False)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    match = re.search(r"^_NET_WM_PID\(CARDINAL\) = (\d+)$", result.stdout, re.M)
    if result.returncode or not match or int(match[1]) != owner_pid:
        return {}
    class_match = re.search(r'^WM_CLASS\(STRING\) = (.+)$', result.stdout, re.M)
    return {"id": hex(window_id), "class": class_match[1] if class_match else "",
            "title": "", "source": "WINDOWID"}


def foreground_process(attached):
    """Prefer a leaf of the foreground process group over its shell leader."""
    group = [process for process in attached if process["tpgid"] > 0 and
             process["pgid"] == process["tpgid"]]
    if group:
        parents = {process["ppid"] for process in group}
        leaves = [process for process in group if process["pid"] not in parents]
        return max(leaves or group, key=lambda process: (process["start_ticks"], process["pid"]))
    return attached[-1] if attached else None


def scan(proc_root=Path("/proc"), pts_root=Path("/dev/pts"), include_windows=True):
    """Return one record per active PTY, starting with the PTY devices."""
    try:
        ptys = {f"/dev/pts/{name}" for name in os.listdir(pts_root)
                if name.isdecimal()}
    except OSError:
        ptys = set()
    try:
        pids = [int(name) for name in os.listdir(proc_root) if name.isdecimal()]
    except OSError:
        pids = []
    briefs = {}
    members = {pty: [] for pty in ptys}
    owners = {pty: [] for pty in ptys}
    for pid in pids:
        brief = process_brief(pid, proc_root)
        if brief is None:
            continue
        briefs[pid] = brief
        if brief["pty"] in members:
            process = process_info(pid, proc_root)
            if process:
                members[brief["pty"]].append(process)

    # Terminal owners are usually named emulators or ancestors of PTY clients.
    # A periodic full sweep still discovers unusual emulators and detached owners.
    real_proc = proc_root == Path("/proc") and pts_root == Path("/dev/pts")
    now = time.monotonic()
    full_sweep = (not real_proc or _owner_scan["ptys"] != ptys or
                  now - _owner_scan["at"] >= OWNER_SWEEP_INTERVAL)
    candidates = set(pids) if full_sweep else set(_owner_scan["pids"])
    candidates.update(pid for pid, brief in briefs.items()
                      if brief["comm"].casefold() in TERMINALS)
    for attached in members.values():
        for process in attached:
            parent = process["ppid"]
            seen = set()
            while parent in briefs and parent not in seen:
                seen.add(parent)
                candidates.add(parent)
                if (parent in _owner_scan["pids"] or
                        briefs[parent]["comm"].casefold() in TERMINALS):
                    break
                parent = briefs[parent]["ppid"]
    found_owners = set()
    for pid in candidates & briefs.keys():
        held = master_ptys(pid, proc_root) & ptys
        if not held:
            continue
        process = process_info(pid, proc_root)
        if process is None:
            continue
        found_owners.add(pid)
        for pty in held:
            owners[pty].append(process)
    if real_proc:
        _owner_scan["pids"] = found_owners
        if full_sweep:
            _owner_scan["ptys"] = ptys
            _owner_scan["at"] = now
    windows = windows_by_pid() if include_windows else {}
    terminals = []
    for pty in sorted(ptys, key=lambda value: int(value.rsplit("/", 1)[1])):
        attached = sorted(members[pty], key=lambda p: p["pid"])
        holders = sorted(owners[pty], key=lambda p: p["pid"])
        if not attached and not holders:
            continue
        known = [p for p in holders if p["comm"].casefold() in TERMINALS]
        owner = (known or holders or [None])[0]
        foreground = foreground_process(attached)
        agents = [p for p in attached if agent_for(p)]
        active_agent = next((p for p in agents if p["pgid"] == p["tpgid"]),
                            agents[0] if agents else None)
        command = foreground or active_agent
        terminal_name = owner["comm"] if owner else "unknown"
        owner_pid = owner["pid"] if owner else 0
        start = owner["start_ticks"] if owner else (attached[0]["start_ticks"] if attached else 0)
        identity = hashlib.sha256(f"{pty}:{owner_pid}:{start}".encode()).hexdigest()[:16]
        owner_windows = windows.get(owner_pid, [])
        # A server process may own several windows. Do not assign one at random.
        window = ({**owner_windows[0], "source": "wmctrl PID"} if len(owner_windows) == 1
                  else window_from_environment(attached, owner_pid) if include_windows else {})
        session_id = ""
        session_id_source = ""
        agent_name = agent_for(active_agent) if active_agent else ""
        if active_agent:
            match = SESSION_ID.search(" ".join(active_agent["argv"]))
            if match:
                session_id = match.group().lower()
                session_id_source = "argv"
            elif proc_root == Path("/proc") and agent_name == "claude":
                session_id = agent_identity.claude_session_id(active_agent)
                session_id_source = "Claude PID file" if session_id else ""
            elif proc_root == Path("/proc") and agent_name == "codex":
                session_id = agent_identity.codex_session_id(active_agent)
                session_id_source = "Codex rollout (inferred)" if session_id else ""
        terminals.append({
            "key": identity, "state": "live", "pty": pty,
            "terminal": terminal_name, "terminal_pid": owner_pid,
            "window_id": window.get("id", ""), "window_class": window.get("class", ""),
            "window_title": window.get("title", ""),
            "window_source": window.get("source", ""),
            "pid": command["pid"] if command else 0,
            "pgid": command["pgid"] if command else 0,
            "sid": command["sid"] if command else 0,
            "comm": command["comm"] if command else "",
            "host_application": command["comm"] if command else "",
            "cwd": command["cwd"] if command else "",
            "pwd_env": command["pwd_env"] if command else "",
            "uid": command["uid"] if command else -1,
            "euid": command["euid"] if command else -1,
            "agent": agent_name,
            "agent_pid": active_agent["pid"] if active_agent else 0,
            "agent_start_ticks": active_agent["start_ticks"] if active_agent else 0,
            "agent_cwd": active_agent["cwd"] if active_agent else "",
            "session_id": session_id, "session_id_source": session_id_source,
            "argv": command["argv"] if command else [],
            "frozen_id": next((p["frozen_id"] for p in attached if p["frozen_id"]), ""),
            "launch_unit": next((p["launch_unit"] for p in attached if p["launch_unit"]), ""),
            "processes": attached,
        })
    return terminals


def read_previous(path=SESSIONS_FILE):
    if not path.exists():
        return {"session": [], "terminal": [], "saved": []}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _source_signature(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _cache_path(path):
    return path.with_suffix(".cache.json")


def _write_previous_cache(path, data, signature=None):
    signature = signature if signature is not None else _source_signature(path)
    if signature is None:
        return
    cache_path = _cache_path(path)
    payload = {"version": 1, "source": signature,
               "inventory": {key: value for key, value in data.items()
                             if key not in {"changed", "scan_status"}}}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=cache_path.parent,
                                         prefix=f".{cache_path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False)
        if _source_signature(path) == signature:
            temporary.replace(cache_path)
    except (OSError, TypeError, ValueError):
        # The TOML remains authoritative if the optional cache cannot be written.
        pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_previous(path=SESSIONS_FILE):
    """Load a validated snapshot, falling back to the authoritative TOML."""
    path = Path(path)
    signature = _source_signature(path)
    if signature is not None:
        try:
            with _cache_path(path).open(encoding="utf-8") as stream:
                cached = json.load(stream)
            if (cached.get("version") == 1 and cached.get("source") == signature
                    and _source_signature(path) == signature):
                inventory = cached["inventory"]
                if all(isinstance(inventory.get(key), list)
                       for key in ("session", "terminal", "saved")):
                    return inventory
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
    previous = read_previous(path)
    if signature is not None and _source_signature(path) == signature:
        _write_previous_cache(path, previous, signature)
    return previous


@lru_cache(maxsize=32768)
def recorded_at(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def reconcile(current, previous, now=None):
    now = now or datetime.now(timezone.utc)
    timestamp = now.isoformat()
    cutoff = now - RETENTION
    by_key = {row["key"]: row.copy() for row in previous.get("terminal", [])}
    for row in by_key.values():
        if row["state"] == "live" or not row.get("closed_at"):
            row["closed_at"] = timestamp
        row["state"] = "dormant"
    for row in current:
        row = row.copy()
        old = by_key.get(row["key"])
        history = list(old.get("command_history", [])) if old else []
        if (old and (old.get("pid"), old.get("argv")) !=
                (row.get("pid"), row.get("argv")) and old.get("argv")
                and old.get("comm", "").casefold() not in SHELLS):
            history.append({
                "pid": old["pid"], "comm": old.get("comm", ""),
                "argv": old["argv"], "cwd": old.get("cwd", ""),
                "ended_at": timestamp,
            })
        row["command_history"] = [entry for entry in history
                                  if (ended := recorded_at(entry.get("ended_at")))
                                  and ended >= cutoff]
        by_key[row["key"]] = row
    by_key = {key: row for key, row in by_key.items()
              if row["state"] == "live" or
              (closed := recorded_at(row.get("closed_at"))) and closed >= cutoff}
    sessions = {row["id"]: row.copy() for row in previous.get("session", [])
                if row.get("source") != "hotlist"}
    live_ids = set()
    for terminal in current:
        if not terminal["agent"]:
            continue
        session_id = terminal["session_id"] or f"process:{terminal['key']}"
        live_ids.add(session_id)
        sessions.setdefault(session_id, {
            "id": session_id, "agent": terminal["agent"],
            "repo": terminal["cwd"], "title": "",
            "state": "live", "source": "process",
        })
    for row in sessions.values():
        if row["id"] in live_ids:
            row["state"] = "live"
            row.pop("closed_at", None)
        else:
            row["state"] = "dormant"
            if not row.get("closed_at"):
                row["closed_at"] = timestamp
    sessions = {key: row for key, row in sessions.items()
                if row["state"] == "live" or
                (closed := recorded_at(row.get("closed_at"))) and closed >= cutoff}
    for row in by_key.values():
        row["command_history"] = [entry for entry in row.get("command_history", [])
                                  if (ended := recorded_at(entry.get("ended_at")))
                                  and ended >= cutoff]
    live_frozen_ids = {row.get("frozen_id") for row in current if row.get("frozen_id")}
    live_terminal_keys = {row["key"] for row in current}
    saved = [row.copy() for row in previous.get("saved", [])]
    for row in saved:
        row["state"] = ("live" if row["id"] in live_frozen_ids or
                        row.get("source_terminal_key") in live_terminal_keys else "saved")
    return {"session": sorted(sessions.values(), key=lambda row: row["id"]),
            "terminal": sorted(by_key.values(), key=lambda row: (row["state"] != "live", row["pty"])),
            "saved": saved}


def write_inventory(data, path=SESSIONS_FILE):
    scanned_at = datetime.now(timezone.utc).isoformat()
    canonical = {"scanned_at": scanned_at, "session": [], "saved": [], "terminal": []}
    lines = ["# Owned by sessionlaunch. Observed terminals, agent processes, and frozen commands.",
             f"scanned_at = {toml_string(scanned_at)}", ""]
    for row in data["session"]:
        written = {}
        lines.append("[[session]]")
        for field in ("id", "agent", "repo", "title", "state", "source", "closed_at"):
            written[field] = str(row.get(field, ""))
            lines.append(f"{field} = {toml_string(written[field])}")
        canonical["session"].append(written)
        lines.append("")
    for row in data.get("saved", []):
        written = {}
        lines.append("[[saved]]")
        for field in ("id", "title", "directory", "captured_at", "source_terminal_key",
                      "state", "replay_policy", "shell", "shell_source"):
            written[field] = str(row.get(field, ""))
            lines.append(f"{field} = {toml_string(written[field])}")
        written["command_argv"] = row.get("command_argv", [])
        lines.append(f"command_argv = {json.dumps(written['command_argv'], ensure_ascii=False)}")
        canonical["saved"].append(written)
        lines.append("")
    for row in data["terminal"]:
        written = {}
        lines.append("[[terminal]]")
        for field in ("key", "state", "pty", "terminal", "terminal_pid", "window_id",
                      "window_class", "window_title", "window_source", "pid", "pgid", "sid", "comm",
                      "host_application", "cwd", "pwd_env", "uid", "euid", "agent",
                      "agent_pid", "agent_start_ticks", "agent_cwd", "session_id",
                      "session_id_source", "frozen_id", "launch_unit", "closed_at"):
            value = row.get(field, -1 if field in {"uid", "euid"} else
                            0 if field in {"terminal_pid", "pid", "agent_pid", "agent_start_ticks",
                                           "pgid", "sid"} else "")
            written[field] = value if isinstance(value, int) else str(value)
            lines.append(f"{field} = {value if isinstance(value, int) else toml_string(value)}")
        written["argv"] = row.get("argv", [])
        lines.append(f"argv = {json.dumps(written['argv'], ensure_ascii=False)}")
        for process in row.get("processes", row.get("process", [])):
            process_written = {}
            lines.append("[[terminal.process]]")
            for field in ("pid", "ppid", "pgid", "sid", "tpgid", "comm", "cwd", "pwd_env",
                          "uid", "euid", "window_id_env"):
                value = process.get(field, -1 if field in {"uid", "euid"} else
                                    0 if field in {"pid", "ppid", "pgid", "sid", "tpgid"} else "")
                process_written[field] = value if isinstance(value, int) else str(value)
                lines.append(f"{field} = {value if isinstance(value, int) else toml_string(value)}")
            written.setdefault("process", []).append(process_written)
        for command in row.get("command_history", []):
            command_written = {}
            lines.append("[[terminal.command_history]]")
            for field in ("pid", "comm", "cwd", "ended_at"):
                value = command.get(field, 0 if field == "pid" else "")
                command_written[field] = value if isinstance(value, int) else str(value)
                lines.append(f"{field} = {value if isinstance(value, int) else toml_string(value)}")
            command_written["argv"] = command.get("argv", [])
            lines.append(f"argv = {json.dumps(command_written['argv'], ensure_ascii=False)}")
            written.setdefault("command_history", []).append(command_written)
        canonical["terminal"].append(written)
        lines.append("")
    contents = "\n".join(lines)
    # scanned_at is deliberately excluded: unchanged observations need no disk write.
    if path.exists() and path.read_text().split("\n", 2)[-1] == contents.split("\n", 2)[-1]:
        return False
    private_write_text(path, contents)
    _write_previous_cache(path, canonical)
    return True


def same_observations(current, previous):
    if (current["session"] != previous.get("session", []) or
            current["saved"] != previous.get("saved", [])):
        return False
    old_terminals = previous.get("terminal", [])
    if len(current["terminal"]) != len(old_terminals):
        return False
    for new, old in zip(current["terminal"], old_terminals):
        if new == old:
            continue
        # Window titles may change every second. Keep them current in the UI,
        # but persist the latest title with the next substantive observation.
        if new.keys() != old.keys() or any(
                new[key] != old[key] for key in new if key != "window_title"):
            return False
    return True


def update(path=SESSIONS_FILE):
    path = Path(path)
    if path == SESSIONS_FILE:
        secure_config_dir()
    if path == SESSIONS_FILE and not path.exists() and LEGACY_SESSIONS_FILE.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=path.parent,
                                             prefix=".sessions-migrate-", delete=False) as stream:
                temporary = Path(stream.name)
                with LEGACY_SESSIONS_FILE.open("rb") as source:
                    shutil.copyfileobj(source, stream)
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    try:
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        signature = None
    cached = _previous_cache.get(path)
    previous = cached[1] if cached and cached[0] == signature else load_previous(path)
    links = manual_links.load()
    manual_removed = any(row.get("session_id_source") == "manual" and row["key"] not in links
                         for row in previous.get("terminal", []))
    if manual_removed:
        # The cached inventory is also held by the GUI. Never mutate it in place.
        previous = {**previous, "terminal": [row.copy() for row in previous["terminal"]]}
        for row in previous["terminal"]:
            if row.get("session_id_source") == "manual" and row["key"] not in links:
                row["session_id"] = ""
                row["session_id_source"] = ""
    # Retire imported hotlist rows even when the PTY namespace is unavailable.
    imported = [row for row in previous.get("session", []) if row.get("source") == "hotlist"]
    if imported:
        previous["session"] = [row for row in previous["session"]
                               if row.get("source") != "hotlist"]
        write_inventory(previous, path)
    # An empty /dev/pts inside a sandbox is not evidence that host terminals exited.
    if (any(row.get("state") == "live" for row in previous.get("terminal", []))
            and not any(name.isdecimal() for name in os.listdir("/dev/pts"))):
        if manual_removed:
            write_inventory(previous, path)
        return {**previous, "scan_status": "unavailable",
                "changed": bool(imported or manual_removed)}
    current = scan()
    manual_links.apply(current, links)
    data = reconcile(current, previous)
    # A routine poll normally has no new observation. Comparing the in-memory
    # records avoids formatting and comparing the entire history TOML each tick.
    unchanged = same_observations(data, previous)
    data["changed"] = False if unchanged else write_inventory(data, path)
    data["scan_status"] = "ok"
    stat = path.stat()
    _previous_cache[path] = ((stat.st_mtime_ns, stat.st_size), data)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect PTYs and update sessions.toml")
    parser.add_argument("--show", action="store_true", help="print a short terminal summary")
    args = parser.parse_args()
    inventory = update()
    if args.show:
        if inventory.get("scan_status") == "unavailable":
            print("PTY namespace unavailable; showing last saved observation")
        for terminal in inventory["terminal"]:
            print(f"{terminal['state']:7} {terminal['pty']:12} "
                  f"{terminal['terminal']:16} {terminal['comm']:16} "
                  f"pgid={terminal['pgid']} cwd={terminal['cwd']}")
