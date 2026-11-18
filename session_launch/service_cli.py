#!/usr/bin/env python3
"""Inspect and control the SessionLaunch user service."""

import argparse
import json
from pathlib import Path

from .sessionlaunch_paths import SERVICE_SOCKET
from .sessionlaunch_service_client import request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=SERVICE_SOCKET)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--json", action="store_true")
    for name in ("raise", "freeze"):
        command = commands.add_parser(name)
        command.add_argument("terminal_key")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            answer = request("status", path=args.socket)
            print(f"service version={answer.get('version', 'unknown')} pid={answer['pid']} "
                  f"revision={answer['revision']} "
                  f"{'scanning' if answer['warming'] else 'ready'}")
            if answer["error"]:
                print(f"scan error: {answer['error']}")
        elif args.command == "snapshot":
            answer = request("snapshot", path=args.socket, timeout=5)
            if args.json:
                print(json.dumps(answer))
            elif answer["data"] is None:
                print("Service is scanning terminals")
            else:
                data = answer["data"]
                print(f"revision={answer['revision']} scan={data.get('scan_status', 'unknown')} "
                      f"terminals={len(data.get('terminal', []))} "
                      f"live={sum(row.get('state') == 'live' for row in data.get('terminal', []))} "
                      f"frozen={len(data.get('saved', []))}")
        else:
            answer = request(args.command, path=args.socket, timeout=5,
                             terminal_key=args.terminal_key)
            print(answer.get("window_id") or answer.get("saved", {}).get("id") or "ok")
    except (OSError, ValueError) as error:
        parser.exit(1, f"sessionlaunch service: {error}\n")


if __name__ == "__main__":
    main()
