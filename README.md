# SessionLaunch

SessionLaunch is a Python and PySide6 desktop prototype for finding, organizing,
and returning to terminal and agent sessions. It inventories local PTYs, relates
known Claude Code and Codex conversations to terminals, and keeps notes and
groups available after a window closes. An optional user service maintains the
inventory while the GUI is closed.

The prototype is built for a Linux desktop with X11 window management. It
does not restore terminal screen contents, unsaved program state, or window
geometry. Native Wayland window activation depends on compositor support.

[Read the documentation](https://toppk.github.io/sessionlaunch/) ·
[Session model](docs/session-model.md) · [Changelog](CHANGELOG.md) ·
[MIT license](LICENSE)

## Install and run

Use Python 3.11 or newer, PySide6, python-xlib, Pandoc only for the documentation
site, and `systemd --user` for the default terminal launch backend. X11 window
controls use `wmctrl`; `xprop` helps verify xterm-style windows. A supported
terminal emulator is also required: WezTerm, foot, Ghostty, xterm, or Revenant.

Install this checkout in the Python environment you intend to use:

```sh
python3 -m pip install --user --no-build-isolation .
sessionlaunch install
sessionlaunch gui
```

The install requires `setuptools>=80` for a build without isolation. You can
instead use an isolated build if your package installer can fetch build
requirements. The source checkout also supports `python3 sessionlaunch.py` for
development. `sessionlaunch install` writes and starts a user systemd service;
run `sessionlaunch status` to check it. The GUI works without that service but
scans PTYs itself.

`sessionlaunch describe` lists command effects; `--format json` gives the same
list to scripts. Useful commands include:

```sh
sessionlaunch status
sessionlaunch snapshot
sessionlaunch sessions list --state dormant --limit 20
sessionlaunch hotlist --hotlist PATH --search TEXT
sessionlaunch request-revive --hotlist PATH --id UUID
sessionlaunch --version
```

`hotlist` only inspects an existing historical TOML file. `request-revive`
sends stopped matches to a running GUI, which asks for confirmation before
starting each agent. Neither command edits the hotlist. The GUI also previews
the directory and command before an agent revival. A frozen foreground
command can be replayed after confirmation, but a frozen Claude or Codex
command is preview-only; agent conversations use their session IDs instead.

## Organize sessions

The Sessions column shows known agents and terminal attachments. Plain
terminals and frozen commands have separate views. Select a row to inspect
its session identity, current process and PTY, command line, and window
controls. Double-click a live terminal to raise a verified X11 window.

Use domains, groups, and sections to organize records independently of their
repositories. A session can belong to multiple groups and can be placed on
the Standing shelf. Standing is a visible shelf, not an automatic restart
policy. New sessions are Kept by default. Setting one aside keeps its notes
and memberships while hiding it from the usual views.

Press F2 to edit a one-line annotation. `r:role`, `o:/scope`, and `t:/task`
markers are optional, and the rest of the line is preserved as a note:

```text
Review the API handoff. r:maintainer o:/api t:/auth
```

Groups and domains offer a reboot checklist. “At boot: Always” preselects a
session; “Ask” leaves it clear; “Never” hides it in a collapsed section.
Every launch still needs confirmation. If an observed agent stops, “Tell me”
shows a notice without restarting it.

## Data and operation

SessionLaunch stores its inventory and organization under
`$XDG_CONFIG_HOME/sessionlaunch`, or `~/.config/sessionlaunch` by default.
The configuration directory is private (0700), and data files are written
0600. The service socket is under `$XDG_RUNTIME_DIR/sessionlaunch`. The
service scans PTYs every five seconds; commands shorter than a scan interval
may be missed. Closed terminal history expires after 90 days. Inventory
records include command arguments and working directories, so treat the
configuration directory as private data.

The default launch backend uses transient `systemd --user` units, leaving
terminals independent of the GUI. `launch_backend = "direct"` in
`settings.toml` selects the older direct process path. A preferred terminal
can be chosen in the GUI or set with `default_terminal` in the same file.

`sessionlaunch sessions` searches stored history without starting programs.
`sessionlaunch freeze KEY` saves an observed foreground command;
`sessionlaunch raise KEY` raises a verified X11 window. Run
`sessionlaunch COMMAND --help` before either action to see its arguments.

## Development

The version has one source of truth: `session_launch/__init__.py`. The package
build reads that value, and `sessionlaunch --version`, `sessionlaunch version`,
and the GUI's **About SessionLaunch** menu display it. `sessionlaunch status`
shows the running service's version, which may differ until it is restarted.
For a new release, update
that value and [CHANGELOG.md](CHANGELOG.md), verify the wheel's metadata, then
tag the reviewed commit `vMAJOR.MINOR.PATCH`. Use a minor bump for new behavior
and a patch bump for fixes during the 0.x series. A tag or push is a separate
release step; changing the version does not publish anything.

To verify a wheel before release, build it into an empty directory and check
that its filename and `METADATA` both carry the new version:

```sh
python3 -m pip wheel --no-deps --no-build-isolation -w /tmp/sessionlaunch-wheel .
```

Run the focused tests with `python3 -m unittest discover -p 'test_*.py'`.
The disposable X11 restore cycle is opt-in:

```sh
SESSIONLAUNCH_INTEGRATION=1 xvfb-run -a python3 -m unittest -v test_restore_cycle
```

It uses a harmless Python marker and does not launch an agent. Build the
Horizon documentation site with `site/build.sh`; the Pages workflow
publishes that directory after the repository's Pages source is set to
GitHub Actions.

SessionLaunch code and documentation are licensed under the [MIT license](LICENSE).
The logo is reserved for SessionLaunch branding; see [asset rights](ASSETS.md).
The bundled Horizon font licenses are in `site/theme/fonts/`.
