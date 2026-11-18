"""Explicit terminal-to-agent links, scoped to one observed agent process."""

import json
import os
import tempfile
from pathlib import Path

from .session_identity import agent_session_key


FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / \
    "sessionlaunch" / "manual_links.json"


def load(path=None):
    path = Path(path or FILE)
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Manual link file must be an object")
    links = {}
    for key, row in data.items():
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        provider = row.get("provider")
        session_id = row.get("session_id")
        if (not agent_session_key(provider, session_id) or
                not isinstance(row.get("agent_pid"), int) or row["agent_pid"] <= 0 or
                not isinstance(row.get("agent_start_ticks"), int) or
                row["agent_start_ticks"] <= 0 or
                not isinstance(row.get("cwd"), str) or not row["cwd"]):
            continue
        links[key] = row
    return links


def save(links, path=None):
    path = Path(path or FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent,
                                         prefix=".manual-links-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(links, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def matches(terminal, link):
    return (terminal.get("state") == "live" and terminal.get("key") == link.get("terminal_key")
            and terminal.get("agent") == link.get("provider")
            and terminal.get("agent_pid") == link.get("agent_pid")
            and terminal.get("agent_start_ticks") == link.get("agent_start_ticks")
            and os.path.realpath(terminal.get("agent_cwd") or terminal.get("cwd") or "") ==
            os.path.realpath(link.get("cwd") or ""))


def apply(terminals, links):
    """Apply only to the exact process the user confirmed; never override a conflicting UUID."""
    for terminal in terminals:
        link = links.get(terminal.get("key"))
        if (link and matches(terminal, link) and
                terminal.get("session_id") in ("", link["session_id"])):
            terminal["session_id"] = link["session_id"]
            terminal["session_id_source"] = "manual"
