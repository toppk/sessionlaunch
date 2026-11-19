---
title: Getting started
eyebrow: Start
lede: Install the Python prototype, start its user service, and open the desktop window.
description: Install and operate SessionLaunch on a Linux desktop.
---

# Getting started

## Requirements

- Linux with Python 3.11 or newer, PySide6, and python-xlib.
- An X11 window manager for SessionLaunch's full window controls.
- `systemd --user` for the default independent launch backend.
- `wmctrl`, and optionally `xprop`, for X11 window discovery.
- WezTerm, foot, Ghostty, xterm, or Revenant to open new terminals.
- AgentsView for the historical Claude Code and Codex catalog.

## Install

From a source checkout, build and install the wheel in the Python environment
you intend to run. `setuptools>=80` is needed for the build shown here.

```sh
python3 -m pip wheel --no-deps --no-build-isolation -w dist .
python3 -m pip install --user --no-deps dist/sessionlaunch-*.whl
sessionlaunch install
sessionlaunch status
sessionlaunch gui
sessionlaunch --version
```

`sessionlaunch install` creates a private configuration directory and starts
the user service. The GUI and CLI use that service for session state. For
development in the checkout, run `python3 sessionlaunch.py` with the service
installed from the same checkout.

## Find and organize

The Sessions view combines known agent conversations with their current
terminal attachments. Plain terminals and frozen commands have separate
views. Select a session to see its durable identity and current process
details. Double-click a live X11 terminal to raise it.

Use the navigation pane to create domains and groups. A session can join
several groups. Press F2 to write a one-line annotation, optionally with
`r:role`, `o:/scope`, and `t:/task` markers. The full line is saved.

Kept sessions appear in normal views; setting a session aside keeps its
metadata but hides it from those views. Standing is a visible shelf, not a
restart policy. The reboot checklist shows the agent sessions eligible for
revival and requires confirmation before opening them.

## Inspect from the command line

```sh
sessionlaunch describe
sessionlaunch snapshot
sessionlaunch sessions list --state dormant --limit 20
sessionlaunch hotlist --hotlist PATH --search TEXT
```

`sessionlaunch hotlist` only inspects a historical TOML file. To offer one
stopped conversation for revival in the running GUI, use
`sessionlaunch request-revive --hotlist PATH --id UUID`. The GUI still asks
before launching it. No hotlist command edits the TOML file.

## Stored data

SessionLaunch writes under `$XDG_CONFIG_HOME/sessionlaunch`, normally
`~/.config/sessionlaunch`. The directory is mode 0700 and data files are mode
0600. Its inventory can include terminal commands and arguments. The user
service keeps a socket under `$XDG_RUNTIME_DIR/sessionlaunch` and scans PTYs
every five seconds. Closed terminal observations expire after 90 days.
