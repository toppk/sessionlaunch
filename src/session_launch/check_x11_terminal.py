#!/usr/bin/env python3
"""Check whether SessionLaunch can identify and activate an X11 terminal by PID."""

import argparse
import os
import re
import subprocess
import sys


def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"{' '.join(args)}: {error}") from error
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)}: {result.stderr.strip() or 'failed'}")
    return result.stdout


def property_value(output, name):
    match = re.search(rf"^{re.escape(name)}\([^)]*\) = (.*)$", output, re.M)
    return match.group(1).strip() if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int, help="terminal emulator PID, not the shell PID")
    parser.add_argument("--window-id", help="inspect a known X11 window even if PID lookup fails")
    args = parser.parse_args()
    if args.pid <= 0 or not os.path.exists(f"/proc/{args.pid}"):
        parser.error("PID must be a running local process")

    try:
        listing = command("wmctrl", "-lxp")
        supported = command("xprop", "-root", "_NET_SUPPORTED")
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    windows = []
    for line in listing.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6:
            try:
                window_pid = int(fields[2])
            except ValueError:
                continue
            windows.append((fields[0], window_pid, fields[3], fields[5]))

    matched = [window for window in windows if window[1] == args.pid]
    print(f"Terminal PID: {args.pid}")
    print(f"DISPLAY: {os.environ.get('DISPLAY', '(unset)')}")
    print(f"wmctrl PID matches: {len(matched)}")
    for window_id, _, window_class, title in matched:
        print(f"  {window_id}  {window_class}  {title}")

    candidates = matched
    if args.window_id:
        try:
            requested = int(args.window_id, 16 if args.window_id.lower().startswith("0x") else 10)
        except ValueError:
            parser.error("--window-id must be a decimal or 0x-prefixed X11 window ID")
        candidates = [window for window in windows if int(window[0], 16) == requested]
        if not candidates:
            candidates = [(hex(requested), 0, "", "")]
    elif not matched:
        # A missing _NET_WM_PID appears as PID 0 in wmctrl. An explicit -T
        # title can suggest a window for diagnosis, but never proves ownership.
        try:
            argv = open(f"/proc/{args.pid}/cmdline", "rb").read().decode(
                errors="replace").split("\0")
        except OSError:
            argv = []
        titles = [argv[i + 1] for i, value in enumerate(argv[:-1])
                  if value in ("-T", "-title")]
        candidates = [window for window in windows if window[1] == 0
                      and window[3] in titles]
        if candidates:
            print("Possible title match (not proof of ownership):")
            for window_id, _, window_class, title in candidates:
                print(f"  {window_id}  {window_class}  {title}")

    property_ok = False
    for window_id, _, _, _ in candidates:
        try:
            properties = command("xprop", "-id", window_id, "_NET_WM_PID",
                                 "WM_CLIENT_MACHINE", "WM_CLASS")
        except RuntimeError as error:
            print(f"  {window_id}: ERROR: {error}")
            continue
        pid_value = property_value(properties, "_NET_WM_PID")
        machine = property_value(properties, "WM_CLIENT_MACHINE")
        window_class = property_value(properties, "WM_CLASS")
        print(f"  {window_id} _NET_WM_PID: {pid_value or 'MISSING'}")
        print(f"  {window_id} WM_CLIENT_MACHINE: {machine or 'MISSING'}")
        print(f"  {window_id} WM_CLASS: {window_class or 'MISSING'}")
        property_ok |= pid_value == str(args.pid) and bool(machine)

    activation = "_NET_ACTIVE_WINDOW" in supported
    print(f"Window manager _NET_ACTIVE_WINDOW: {'supported' if activation else 'not advertised'}")
    ready = len(matched) == 1 and property_ok and activation
    print(f"SessionLaunch PID lookup: {'PASS' if ready else 'FAIL'}")
    if not matched:
        print("Reason: no listed X11 window advertises this PID as _NET_WM_PID.")
    elif len(matched) > 1:
        print("Reason: this PID owns multiple windows; SessionLaunch needs a unique window ID.")
    elif not property_ok:
        print("Reason: _NET_WM_PID or WM_CLIENT_MACHINE is missing or incorrect.")
    elif not activation:
        print("Reason: the window manager does not advertise _NET_ACTIVE_WINDOW.")
    print("No activation request was sent; compositor focus policy is not tested.")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
