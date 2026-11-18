"""One installed command for the GUI, service, and inspection tools."""

import argparse
import importlib
import json
import os
import sys
import time
from dataclasses import dataclass

from . import __version__


@dataclass(frozen=True)
class Command:
    effect: str
    summary: str
    module: str
    prefix: tuple[str, ...] = ()
    arguments: str = ""


# Discovery and dispatch use the same table. Effects describe what running a
# command can do, including commands that request a later GUI confirmation.
COMMANDS = {
    "describe": Command("observe", "Describe available commands and effects", "cli", arguments="[COMMAND]"),
    "version": Command("observe", "Show the SessionLaunch version", "cli"),
    "gui": Command("open-window", "Open the SessionLaunch window (default)", "gui"),
    "daemon": Command("run-service", "Run the terminal inventory service", "daemon"),
    "install": Command("write-and-start-service", "Install and start the user service", "installer"),
    "status": Command("observe", "Show service status", "service_cli", ("status",)),
    "snapshot": Command("observe", "Summarize or export the live inventory", "service_cli", ("snapshot",)),
    "raise": Command("raise-window", "Raise a verified X11 terminal window", "service_cli", ("raise",), "KEY"),
    "freeze": Command("save-record", "Save a live terminal's foreground command", "service_cli", ("freeze",), "KEY"),
    "sessions": Command("observe", "Search stored terminal and agent history", "sessions_cli", arguments="..."),
    "hotlist": Command("observe", "Check a historical hotlist", "hotlist_cli", arguments="..."),
    "request-revive": Command("request-gui-confirmation", "Ask the GUI to offer stopped hotlist sessions for revival", "hotlist_cli", arguments="..."),
}


def usage():
    width = max(len(name + (" " + spec.arguments if spec.arguments else ""))
                for name, spec in COMMANDS.items())
    lines = ["usage: sessionlaunch [command] [arguments]", "", "Commands:"]
    for name, spec in COMMANDS.items():
        label = name + (" " + spec.arguments if spec.arguments else "")
        lines.append(f"  {label:<{width}}  {spec.summary}")
    lines.extend(("", "Run `sessionlaunch describe [COMMAND]` for effects and details.",
                  "Run `sessionlaunch COMMAND --help` for command-specific options.",
                  "Run `sessionlaunch --version` for the installed version."))
    return "\n".join(lines)


def describe(argv):
    parser = argparse.ArgumentParser(prog="sessionlaunch describe",
                                     description="Describe commands from the dispatch table")
    parser.add_argument("command", nargs="?", choices=tuple(COMMANDS))
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    rows = [{"name": name, "effect": spec.effect, "summary": spec.summary,
             "arguments": spec.arguments}
            for name, spec in COMMANDS.items() if args.command in (None, name)]
    if args.format == "json":
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"{row['name']}{' ' + row['arguments'] if row['arguments'] else ''}"
                  f" [{row['effect']}] — {row['summary']}")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if argv and argv[0] in ("-V", "--version"):
        if len(argv) != 1:
            raise SystemExit("usage: sessionlaunch --version")
        print(f"sessionlaunch {__version__}")
        return 0
    command = argv.pop(0) if argv else "gui"
    spec = COMMANDS.get(command)
    if spec is None:
        raise SystemExit(f"Unknown command: {command}\nRun `sessionlaunch describe` to list commands.")
    if command == "describe":
        return describe(argv)
    if command == "version":
        if argv:
            raise SystemExit("usage: sessionlaunch version")
        print(f"sessionlaunch {__version__}")
        return 0
    if command == "gui":
        if argv:
            raise SystemExit("usage: sessionlaunch gui")
        # PySide is imported only for this command.
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        print(f"{stamp}.{int(now % 1 * 1000):03d} | INFO     | "
              f"sessionlaunch.window - process starting version={__version__} "
              f"pid={os.getpid()}", flush=True)
    module = importlib.import_module(f".{spec.module}", __package__)
    if command in ("hotlist", "request-revive"):
        return module.main(argv, action="request-revive" if command == "request-revive" else "observe")
    return module.main([*spec.prefix, *argv])


if __name__ == "__main__":
    raise SystemExit(main())
