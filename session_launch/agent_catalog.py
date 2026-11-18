"""Small, read-only adapters for AgentsView session records."""

import re
import shlex
import os
from pathlib import Path

from .session_identity import agent_session_key
from .time_display import format_datetime


SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def resume_argv(row):
    """Build validated arguments for a resumable agent session."""
    agent = row.get("agent", "")
    session_id = str(row.get("id", "")).removeprefix("codex:")
    if not SESSION_ID.fullmatch(session_id):
        return []
    if agent == "claude":
        return ["claude", "--resume", session_id]
    if agent == "codex":
        return ["codex", "resume", session_id]
    return []


def proposed_shell_command(row):
    argv = resume_argv(row)
    cwd = row.get("cwd", "")
    if not argv or not cwd:
        return ""
    return f"cd -- {shlex.quote(cwd)} && {shlex.join(argv)}"


def linked_terminal(row, terminals):
    session_id = str(row.get("id", "")).removeprefix("codex:").lower()
    return next((terminal for terminal in terminals
                 if terminal.get("state") == "live" and terminal.get("agent") == row.get("agent")
                 and terminal.get("session_id", "").lower() == session_id), None)


def unlinked_running_terminal(row, terminals):
    """A same-folder agent with no proven UUID makes revival ambiguous."""
    provider = row.get("agent")
    cwd = row.get("cwd")
    if provider not in ("claude", "codex") or not cwd:
        return None
    target = os.path.realpath(cwd)
    return next((terminal for terminal in terminals
                 if terminal.get("state") == "live" and terminal.get("agent") == provider
                 and not agent_session_key(provider, terminal.get("session_id", ""))
                 and (terminal.get("agent_cwd") or terminal.get("cwd"))
                 and os.path.realpath(terminal.get("agent_cwd") or terminal.get("cwd")) == target), None)


def local_time(value):
    return format_datetime(value)


def directory_available(row):
    cwd = row.get("cwd", "")
    return bool(cwd and Path(cwd).is_dir())
