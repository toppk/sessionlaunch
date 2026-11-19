# Changelog

SessionLaunch uses `MAJOR.MINOR.PATCH` version numbers. During the 0.x series,
minor versions add features or change behavior; patch versions fix defects
without intentionally changing the session data format.

## 0.3.1 — 2026-11-19

- Fix repository tag requests so the repository path is sent as data rather
  than mistaken for the service socket path.

## 0.3.0 — 2026-11-19

- Make the per-user service the owner of session inventory, organization,
  settings, links, agent and peer queries, terminal launches, and X11 actions.
- Route the GUI and CLI through the service socket for session state; keep
  appearance preferences local to the GUI.
- Use revision checks for organization writes and queue hotlist requests in
  the service for GUI confirmation.

## 0.2.2 — 2026-11-18

- Exchange GUI/client, service, and protocol versions on service requests and log
  each new GUI connection.

## 0.2.1 — 2026-11-18

- Reload external group changes in the GUI while it is open.

## 0.2.0 — 2026-11-18

- Show the version in the CLI, GUI About dialog, and service status.
- Build the wheel version from the same source value shown at runtime.

## 0.1.0 — 2026-11-18

- Initial public prototype with terminal inventory, agent session organization,
  confirmed revival, a user service, and a documentation site.
