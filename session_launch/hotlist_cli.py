#!/usr/bin/env python3
"""Check the historical hotlist or request GUI confirmation for revival."""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

from . import agent_identity
from . import hotlist_ipc
from . import inventory
from .time_display import format_datetime


from .sessionlaunch_paths import CONFIG_DIR


HOTLIST = (CONFIG_DIR / "hotlist.toml" if (CONFIG_DIR / "hotlist.toml").exists()
           else Path.cwd() / "hotlist.toml")


def live_agents(proc_root=Path("/proc")):
    """Ask the service for its current view of agent processes."""
    if proc_root == Path("/proc"):
        from . import sessionlaunch_service_client

        return sessionlaunch_service_client.request("live_agents", client="cli")["agents"]
    return scan_live_agents(proc_root)


def scan_live_agents(proc_root=Path("/proc")):
    """Service-side process scan, including agents without a visible PTY."""
    try:
        pids = [int(entry.name) for entry in proc_root.iterdir() if entry.name.isdecimal()]
    except OSError as error:
        raise RuntimeError(f"Cannot scan {proc_root}: {error}") from error
    agents = []
    for pid in pids:
        process = inventory.process_info(pid, proc_root)
        if not process or process["uid"] != os.getuid():
            continue
        kind = inventory.agent_for(process)
        if not kind:
            continue
        match = inventory.SESSION_ID.search(" ".join(process["argv"]))
        if match:
            session_id, source = match.group().lower(), "argv"
        elif proc_root == Path("/proc") and kind == "claude":
            session_id = agent_identity.claude_session_id(process)
            source = "Claude PID file" if session_id else ""
        elif proc_root == Path("/proc") and kind == "codex":
            session_id = agent_identity.codex_session_id(process)
            source = "Codex rollout (inferred)" if session_id else ""
        else:
            session_id, source = "", ""
        agents.append({"agent": kind, "id": session_id, "source": source,
                       "pid": pid, "pty": process["pty"], "cwd": process["cwd"]})
    return agents


def repo_contains(repo, cwd):
    if not repo or not cwd:
        return False
    try:
        Path(cwd).relative_to(Path(repo))
        return True
    except ValueError:
        return False


def classify(rows, processes):
    result = []
    for row in rows:
        kind = str(row.get("agent", "")).lower()
        session_id = str(row.get("id", "")).lower()
        exact = [process for process in processes
                 if process["agent"] == kind and process["id"] == session_id]
        possible = [process for process in processes
                    if process["agent"] == kind and not process["id"]
                    and repo_contains(row.get("repo", ""), process["cwd"])]
        status = "running" if exact else "unverified" if possible else "stopped"
        result.append({**row, "status": status,
                       "processes": exact if exact else possible if possible else []})
    return result


def select_rows(rows, args):
    selected = [row for row in rows
                if (args.only == "all" or row["status"] == args.only)
                and (args.agent == "all" or row["agent"] == args.agent)
                and (not args.id or row["id"].lower() == args.id)
                and (not args.search or args.search.casefold() in " ".join(
                    str(row.get(key, "")) for key in ("agent", "repo", "title", "id")
                ).casefold())]
    if args.sort == "status":
        selected.sort(key=lambda row: ("running", "unverified", "stopped").index(row["status"]))
    elif args.sort == "repo":
        selected.sort(key=lambda row: (row.get("repo", "").casefold(), row.get("agent", "")))
    elif args.sort == "last-active":
        selected.sort(key=lambda row: row.get("last_active", ""), reverse=True)
    return selected


def main(argv=None, *, action="observe"):
    """Run a fixed effect selected by the command, never by an option."""
    if action not in ("observe", "request-revive"):
        raise ValueError(f"Unknown hotlist action: {action}")
    argv = list(argv) if argv is not None else list(sys.argv[1:])
    parser = argparse.ArgumentParser(
        prog=f"sessionlaunch {'request-revive' if action == 'request-revive' else 'hotlist'}",
        description=("Ask the running GUI to offer stopped hotlist sessions for revival. "
                     "Each launch still requires GUI confirmation."
                     if action == "request-revive" else
                     "Inspect historical hotlist sessions and their live process status."))
    parser.add_argument("--hotlist", type=Path, default=HOTLIST, help="hotlist TOML path")
    parser.add_argument("--only", choices=("all", "running", "unverified", "stopped"), default="all")
    parser.add_argument("--agent", choices=("all", "claude", "codex"), default="all")
    parser.add_argument("--search", default="", help="search agent, repo, title, or session ID")
    parser.add_argument("--id", default="", help="select one exact session UUID (codex: prefix accepted)")
    parser.add_argument("--sort", choices=("file", "status", "repo", "last-active"), default="file")
    if action == "observe":
        parser.add_argument("--json", action="store_true", help="machine-readable output")
    elif "--json" in argv:
        parser.error("request-revive does not produce JSON; use `sessionlaunch hotlist --json` to inspect")
    if "--revive" in argv:
        parser.error("--revive changes the command's effect; use `sessionlaunch request-revive` instead")
    args = parser.parse_args(argv)
    revive = action == "request-revive"
    if args.id:
        args.id = args.id.casefold().removeprefix("codex:")
        if not inventory.SESSION_ID.fullmatch(args.id):
            parser.error("--id must be a complete session UUID")
    if revive and not (args.search or args.id):
        parser.error("request-revive requires --search or --id")
    try:
        with args.hotlist.open("rb") as stream:
            rows = tomllib.load(stream)["session"]
        if not isinstance(rows, list):
            raise ValueError("session must be a list of TOML tables")
        if args.id and not any(str(row.get("id", "")).lower() == args.id for row in rows):
            raise ValueError(f"Session ID is not in the hotlist: {args.id}")
        processes = live_agents()
    except (OSError, ValueError, KeyError, RuntimeError, tomllib.TOMLDecodeError) as error:
        parser.exit(2, f"sessionlaunch-hotlist: {error}\n")
    selected = select_rows(classify(rows, processes), args)
    if revive:
        candidates = [row for row in selected if row["status"] == "stopped"]
        if args.id and len(candidates) > 1:
            parser.error("--id matched multiple hotlist entries; no request was sent")
        if not candidates:
            print("No stopped sessions matched; nothing was sent to SessionLaunch.")
            return 0
        try:
            queued = hotlist_ipc.request_revive(candidates)
        except (OSError, ValueError) as error:
            parser.exit(2, f"sessionlaunch request-revive: could not queue revivals: {error}\n"
                        "Start the GUI with `sessionlaunch gui` first.\n")
        print(f"Sent {queued} session{'s' if queued != 1 else ''} to SessionLaunch. "
              "Confirm each revival in the GUI; no agent was started by this checker.")
        return 0
    if action == "observe" and args.json:
        print(json.dumps(selected, indent=2, ensure_ascii=False))
    else:
        for row in selected:
            suffix = ", ".join(f"PID {p['pid']} {p['pty'] or 'no PTY'}"
                               + (f" [{p['source']}]" if p["source"] else "")
                               for p in row["processes"])
            print(f"{row['status'].upper():10} {row['agent']:6} "
                  f"{row['title']}  [{row['id']}]")
            print(f"           {row['repo']}  ·  {format_datetime(row.get('last_active'))}"
                  + (f"  ·  {suffix}" if suffix else ""))
        counts = {status: sum(row["status"] == status for row in selected)
                  for status in ("running", "unverified", "stopped")}
        print(f"{len(selected)} sessions: {counts['running']} running, "
              f"{counts['unverified']} unverified, {counts['stopped']} stopped")
    return 0


if __name__ == "__main__":
    arguments = sys.argv[1:]
    selected_action = arguments.pop(0) if arguments and arguments[0] == "request-revive" else "observe"
    raise SystemExit(main(arguments, action=selected_action))
