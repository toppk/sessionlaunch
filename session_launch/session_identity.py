"""Stable group identities for agent and terminal sessions."""

import re


UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def agent_session_key(agent, session_id):
    """An agent session keeps its identity while its process comes and goes."""
    session_id = str(session_id).removeprefix("codex:").lower()
    if agent in ("codex", "claude") and UUID.fullmatch(session_id):
        return f"agent:{agent}:{session_id}"
    return None


def terminal_session_key(terminal):
    """Resolve an observed terminal to its agent, frozen plan, or PTY session."""
    if key := agent_session_key(terminal.get("agent"), terminal.get("session_id", "")):
        return key
    if frozen_id := terminal.get("frozen_id"):
        return f"saved:{frozen_id}"
    return f"terminal:{terminal['key']}"


def terminal_aliases(terminals):
    """Legacy PTY memberships that have a proven stable session identity."""
    aliases = {}
    for terminal in terminals:
        old_key = f"terminal:{terminal['key']}"
        key = terminal_session_key(terminal)
        if key != old_key:
            aliases[old_key] = key
    return aliases
