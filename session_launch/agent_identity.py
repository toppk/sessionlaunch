"""Conservative links from a running agent process to its local session ID."""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path


SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_codex_matches = {}


def claude_session_id(process, sessions_dir=None):
    """Claude writes a PID file with both its session ID and /proc start tick."""
    root = sessions_dir or Path.home() / ".claude" / "sessions"
    try:
        row = json.loads((root / f"{process['pid']}.json").read_text())
    except (OSError, ValueError):
        return ""
    session_id = row.get("sessionId", "")
    if (str(row.get("pid")) != str(process["pid"]) or
            str(row.get("procStart")) != str(process["start_ticks"])
            or not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id)):
        return ""
    if process.get("cwd") and row.get("cwd") != process["cwd"]:
        return ""
    return session_id.lower()


def boot_epoch(proc_stat=Path("/proc/stat")):
    try:
        for line in proc_stat.read_text().splitlines():
            if line.startswith("btime "):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def codex_session_id(process, sessions_dir=None, boot_time=None):
    """Infer a fresh Codex ID only when exactly one rollout started with this process."""
    root = sessions_dir or Path.home() / ".codex" / "sessions"
    start = boot_time if boot_time is not None else boot_epoch()
    if start is None or not process.get("cwd"):
        return ""
    started = start + process["start_ticks"] / os.sysconf("SC_CLK_TCK")
    key = (process["pid"], process["start_ticks"], process["cwd"], str(root))
    cached = _codex_matches.get(key)
    if cached and (cached[0] or time.monotonic() - cached[1] < 5):
        return cached[0]
    matches = []
    try:
        files = root.rglob("rollout-*.jsonl")
        for path in files:
            try:
                if path.stat().st_mtime < started - 10:
                    continue
                with path.open() as stream:
                    row = json.loads(stream.readline())
                if row.get("type") != "session_meta":
                    continue
                meta = row.get("payload", {})
                if meta.get("cwd") != process["cwd"]:
                    continue
                session_id = meta.get("id", "")
                stamp = meta.get("timestamp") or row.get("timestamp", "")
                if not SESSION_ID.fullmatch(session_id):
                    continue
                observed = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                if -5 <= observed - started <= 60:
                    matches.append(session_id.lower())
            except (OSError, ValueError, KeyError, TypeError):
                continue
    except OSError:
        return ""
    result = matches[0] if len(matches) == 1 else ""
    _codex_matches[key] = (result, time.monotonic())
    return result
