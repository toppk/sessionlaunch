"""Preview and explicitly revive one known agent conversation."""

import argparse
import shlex
import sys
import uuid
from pathlib import Path

from .agent_catalog import resume_argv
from .service_client import request
from .session_identity import agent_session_key


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sessionlaunch revive",
        description="Revive one AgentsView session in a new terminal after confirmation.")
    parser.add_argument("provider", choices=("claude", "codex"))
    parser.add_argument("session_id", help="full conversation UUID")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without launching")
    parser.add_argument("--yes", action="store_true", help="confirm the displayed launch plan")
    args = parser.parse_args(argv)
    identity = agent_session_key(args.provider, args.session_id)
    if not identity:
        parser.error("session_id must be a complete conversation UUID")
    session_id = identity.rsplit(":", 1)[-1]
    try:
        payload = request("agent_query", mode="get", session_id=session_id,
                          timeout=30, client="cli")["data"]
        row = payload.get("session", payload) if isinstance(payload, dict) else {}
        if not isinstance(row, dict) or row.get("agent") != args.provider or \
                agent_session_key(row.get("agent"), row.get("id")) != identity:
            raise ValueError("AgentsView did not return the requested provider and session UUID")
        directory = row.get("cwd")
        if not isinstance(directory, str) or not Path(directory).is_dir():
            raise ValueError("AgentsView has no usable local working directory for this session")
        command = shlex.join(resume_argv(row))
        if not command:
            raise ValueError("This session has no supported resume command")
        settings = request("settings_get", client="cli")["settings"]
        terminal = settings["default_terminal"]
        backend = settings["launch_backend"]
    except (OSError, KeyError, ValueError) as error:
        parser.exit(1, f"sessionlaunch revive: {error}\n")

    print(f"Session: {args.provider} {session_id}")
    print(f"Directory: {directory}")
    print(f"Terminal: {terminal} ({backend})")
    print(f"Command: {command}", flush=True)
    if args.dry_run:
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            parser.exit(2, "sessionlaunch revive: confirmation requires a terminal or --yes\n")
        try:
            answer = input("Start this conversation? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled")
            return 0
    token = f"sessionlaunch-{uuid.uuid4().hex}"
    try:
        launched = request(
            "launch_terminal", terminal=terminal, directory=directory,
            title=f"{args.provider} {session_id[:8]} [{token}]",
            command=command, identity=identity, client="cli", timeout=30)
    except (OSError, KeyError, ValueError) as error:
        parser.exit(1, f"sessionlaunch revive: {error}\n")
    print(f"Opening {args.provider} {session_id} (launch {launched['launch_id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
