"""Read Claude's current local peer registrations without contacting the daemon."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)


def process_start(pid, proc_root=Path("/proc")):
    """Use Linux start ticks so a stale registration cannot match a reused PID."""
    try:
        stat = (proc_root / str(pid) / "stat").read_text()
        return stat[stat.rfind(") ") + 2:].split()[19]
    except (OSError, IndexError):
        return ""


def read_peers(root=None, proc_root=Path("/proc")):
    root = root or Path.home() / ".claude" / "sessions"
    peers = {}
    try:
        files = root.glob("*.json")
        for path in files:
            if not path.stem.isdecimal():
                continue
            try:
                if path.stat().st_size > 65536:
                    continue
                row = json.loads(path.read_text())
                pid = int(path.stem)
                session_id = row.get("sessionId", "")
                name = row.get("name", "")
                if (row.get("pid") != pid or not isinstance(session_id, str) or
                        not UUID.fullmatch(session_id) or not isinstance(name, str) or
                        not name or len(name) > 120 or
                        str(row.get("procStart", "")) != process_start(pid, proc_root)):
                    continue
                peer = {"name": name, "session_id": session_id.lower(),
                        "pid": pid, "cwd": str(row.get("cwd") or ""),
                        "kind": str(row.get("kind") or ""),
                        "status": str(row.get("status") or ""),
                        "started_at": row.get("startedAt"),
                        "updated_at": row.get("updatedAt")}
                old = peers.get(peer["session_id"])
                if old is None or (peer["updated_at"] or 0) > (old["updated_at"] or 0):
                    peers[peer["session_id"]] = peer
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    except OSError:
        pass
    return peers


def peer_for(session_id, peers):
    return peers.get(str(session_id).removeprefix("codex:").lower())


def started_at(peer):
    try:
        return datetime.fromtimestamp(int(peer["started_at"]) / 1000,
                                      timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        return ""
