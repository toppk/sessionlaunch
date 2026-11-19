#!/usr/bin/env python3
"""Search the SessionLaunch inventory without starting terminals or agents."""

import argparse
import json
import shlex
import shutil
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .time_display import format_datetime
from .sessionlaunch_service_client import request


def timestamp(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (AttributeError, ValueError):
        return None


def parse_since(value):
    result = timestamp(value)
    if result is None:
        raise argparse.ArgumentTypeError("expected an ISO date, such as 2026-10-01")
    return result


def command_text(argv):
    return shlex.join(argv) if argv else ""


def rows(data, kind):
    scanned_at = data.get("scanned_at", "")
    result = []
    if kind in ("all", "terminal"):
        for record in data.get("terminal", []):
            result.append({
                "kind": "terminal", "id": record["key"], "state": record["state"],
                "when": scanned_at if record["state"] == "live"
                else record.get("closed_at", ""),
                "command": command_text(record.get("argv", [])) or record.get("comm", ""),
                "directory": record.get("cwd", ""), "terminal": record.get("terminal", ""),
                "agent": record.get("agent", ""), "pty": record.get("pty", ""),
                "pid": record.get("terminal_pid", 0), "record": record,
            })
    if kind in ("all", "agent"):
        for record in data.get("session", []):
            result.append({
                "kind": "agent", "id": record["id"], "state": record["state"],
                "when": scanned_at if record["state"] == "live"
                else record.get("closed_at", ""),
                "command": record.get("title") or record.get("agent", ""),
                "directory": record.get("repo", ""), "terminal": "",
                "agent": record.get("agent", ""), "pty": "", "pid": 0, "record": record,
            })
    if kind in ("all", "saved"):
        for record in data.get("saved", []):
            result.append({
                "kind": "saved", "id": record["id"], "state": record["state"],
                "when": record.get("captured_at", ""),
                "command": command_text(record.get("command_argv", [])),
                "directory": record.get("directory", ""), "terminal": "",
                "agent": "", "pty": "", "pid": 0, "record": record,
            })
    return result


def history_rows(data):
    result = []
    for terminal in data.get("terminal", []):
        for phase, command, when in [
            ("current", terminal, data.get("scanned_at", "") if terminal["state"] == "live"
             else terminal.get("closed_at", "")),
            *(("previous", entry, entry.get("ended_at", ""))
              for entry in terminal.get("command_history", [])),
        ]:
            argv = command.get("argv", [])
            if not argv:
                continue
            result.append({
                "kind": "command", "id": terminal["key"], "state": terminal["state"],
                "when": when, "command": command_text(argv),
                "directory": command.get("cwd", ""), "terminal": terminal.get("terminal", ""),
                "agent": terminal.get("agent", ""), "pty": terminal.get("pty", ""),
                "pid": command.get("pid", 0), "terminal_pid": terminal.get("terminal_pid", 0),
                "phase": phase,
                "record": {field: command.get(field) for field in ("pid", "comm", "argv", "cwd")},
            })
    return result


def matches(row, args):
    if args.state and row["state"] not in args.state:
        return False
    if args.terminal and row["terminal"].casefold() != args.terminal.casefold():
        return False
    if args.agent and row["agent"].casefold() != args.agent.casefold():
        return False
    if args.repo and args.repo.casefold() not in row["directory"].casefold():
        return False
    if args.pty and args.pty not in row["pty"]:
        return False
    if args.pid is not None and args.pid not in (
            row["pid"], row.get("terminal_pid", 0), row["record"].get("pid", 0),
            *(process.get("pid", 0) for process in row["record"].get("process", []))):
        return False
    if args.since:
        when = timestamp(row["when"])
        if when is None or when < args.since:
            return False
    searchable = json.dumps(row["record"], ensure_ascii=False, default=str).casefold()
    searchable += " " + " ".join(str(row[key]) for key in
                                   ("kind", "id", "state", "command", "directory", "pty")).casefold()
    searchable += " " + str(row.get("terminal_pid", ""))
    return all(term.casefold() in searchable for term in args.search)


def sorted_rows(found, args):
    if args.sort == "recent":
        found.sort(key=lambda row: timestamp(row["when"]) or datetime.min.replace(
            tzinfo=timezone.utc), reverse=not args.reverse)
    else:
        field = {"command": "command", "directory": "directory", "state": "state",
                 "terminal": "terminal", "pty": "pty", "pid": "pid"}[args.sort]
        found.sort(key=lambda row: row[field] if field == "pid" else
                   str(row[field]).casefold(), reverse=args.reverse)
    return found[:args.limit] if args.limit is not None else found


def short_path(path):
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def clip(value, width):
    return value if len(value) <= width else value[:width - 1] + "…"


def print_table(found, wide=False):
    if not found:
        print("No matching records")
        return
    width = max(100, shutil.get_terminal_size((140, 24)).columns)
    command_width = 75 if wide else max(24, min(65, width - 112))
    print(f"{'WHEN':40} {'KIND':8} {'STATE':8} {'ID':18} "
          f"{'COMMAND':{command_width}} DIRECTORY")
    for row in found:
        date = format_datetime(row["when"])
        kind = row["phase"] if row.get("phase") else row["kind"]
        print(f"{date:40} {kind:8} {row['state']:8} {clip(row['id'], 18):18} "
              f"{clip(row['command'], command_width):{command_width}} "
              f"{short_path(row['directory'])}")


def add_filters(parser):
    parser.add_argument("--state", action="append", help="State; repeat to match several")
    parser.add_argument("--search", action="append", default=[], metavar="TEXT",
                        help="Case-insensitive search, including command history; repeat for AND")
    parser.add_argument("--terminal", help="Exact emulator name, such as xterm")
    parser.add_argument("--agent", choices=("claude", "codex"))
    parser.add_argument("--repo", help="Substring of directory/repository")
    parser.add_argument("--pty", help="PTY substring, such as pts/13")
    parser.add_argument("--pid", type=int, help="Terminal, host, or attached process PID")
    parser.add_argument("--since", type=parse_since, metavar="DATE",
                        help="Only records at or after ISO date/time")
    parser.add_argument("--sort", choices=("recent", "command", "directory", "state",
                                           "terminal", "pty", "pid"), default="recent")
    parser.add_argument("--reverse", action="store_true", help="Reverse sort direction")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true", help="Full matching records as JSON")
    parser.add_argument("--wide", action="store_true", help="Wider command column")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path,
                        help="Inspect an explicit TOML export instead of the live service")
    commands = parser.add_subparsers(dest="action", required=True)
    listing = commands.add_parser("list", help="List terminals, observed agents, or frozen commands")
    listing.add_argument("--kind", choices=("terminal", "agent", "saved", "all"),
                         default="terminal")
    add_filters(listing)
    history = commands.add_parser("history", help="List current and previous PTY commands")
    add_filters(history)
    detail = commands.add_parser("show", help="Show one full record by ID or ID prefix")
    detail.add_argument("kind", choices=("terminal", "agent", "saved"))
    detail.add_argument("id")
    args = parser.parse_args(argv)
    try:
        if args.file:
            with args.file.open("rb") as stream:
                data = tomllib.load(stream)
        else:
            response = request("snapshot", client="cli", timeout=5)
            if response.get("warming") or response.get("data") is None:
                raise ValueError("SessionLaunch service is still scanning")
            data = response["data"]
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as error:
        parser.error(str(error))
    if args.action == "show":
        found = [row for row in rows(data, args.kind) if row["id"].startswith(args.id)]
        if len(found) != 1:
            parser.error(f"ID {args.id!r} matched {len(found)} records; use a longer prefix")
        print(json.dumps(found[0]["record"], indent=2, ensure_ascii=False))
        return 0
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be nonnegative")
    source = history_rows(data) if args.action == "history" else rows(data, args.kind)
    found = sorted_rows([row for row in source if matches(row, args)], args)
    if args.json:
        print(json.dumps(found, indent=2, ensure_ascii=False))
    else:
        print_table(found, args.wide)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
