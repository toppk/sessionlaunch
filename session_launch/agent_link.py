"""Fresh AgentsView lookup for explicit terminal-to-session linking."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import agent_catalog


def _query(*args):
    result = subprocess.run(["agentsview", *args], capture_output=True, text=True,
                            timeout=15, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"AgentsView exited {result.returncode}")
    return json.loads(result.stdout)


def _rows(payload, key):
    rows = payload.get(key, []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"AgentsView returned no {key} list")
    return [row for row in rows if isinstance(row, dict)]


def _started(row):
    value = row.get("started_at") or ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def candidates(provider, cwd):
    """Query all visible matches; never infer or select a session ID."""
    if provider not in ("claude", "codex") or not cwd:
        return []
    target = os.path.realpath(cwd)
    rows = []
    cursor = ""
    seen = set()
    while True:
        args = ["session", "list", "--json", "--limit", "500", "--sort", "started:desc",
                "--agent", provider]
        if cursor:
            args.extend(("--cursor", cursor))
        payload = _query(*args)
        rows.extend(row for row in _rows(payload, "sessions")
                    if row.get("agent") == provider and row.get("cwd")
                    and os.path.realpath(row["cwd"]) == target and agent_catalog.resume_argv(row))
        next_cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
        if not next_cursor or next_cursor in seen:
            break
        seen.add(next_cursor)
        cursor = next_cursor
    rows = list({row["id"]: row for row in rows}.values())
    rows.sort(key=_started, reverse=True)

    def enrich(row):
        result = dict(row)
        if not result.get("started_at") or not result.get("ended_at"):
            try:
                detail = _query("session", "get", row["id"], "--json")
                detail = detail.get("session", detail) if isinstance(detail, dict) else {}
                if isinstance(detail, dict):
                    result.update(detail)
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
                pass
        try:
            messages = _rows(_query("session", "messages", row["id"], "--direction",
                                    "desc", "--limit", "1", "--role", "user,assistant",
                                    "--json"), "messages")
            result["last_message"] = str(messages[0].get("content") or "").strip() if messages else ""
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            result["last_message"] = ""
        return result

    with ThreadPoolExecutor(max_workers=4) as executor:
        enriched = list(executor.map(enrich, rows))
    return sorted((row for row in enriched if row.get("agent") == provider and
                   row.get("cwd") and os.path.realpath(row["cwd"]) == target and
                   agent_catalog.resume_argv(row)), key=_started, reverse=True)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--candidates":
        sys.exit("usage: agent_link.py --candidates PROVIDER CWD")
    try:
        print(json.dumps(candidates(sys.argv[2], sys.argv[3])))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit(str(error))
