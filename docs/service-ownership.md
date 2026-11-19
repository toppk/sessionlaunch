# Service ownership of session state

SessionLaunch has one owner for session state: the per-user service at
`$XDG_RUNTIME_DIR/sessionlaunch/service.socket`. The GUI and CLI are clients.
They do not read or write SessionLaunch's session files, scan PTYs, query agent
transcripts, inspect Claude peer registrations, or control X11 windows on their
own. A client can disappear or restart without becoming a second owner.

## What the service owns

- `sessions.toml`: live PTY observations, command history, frozen commands,
  and the link between a terminal and an agent session.
- `groups.json`: domains, memberships, annotations, ordering, Standing,
  lifecycle choices, and Keep/Set aside. Mutations use a revision so one client
  cannot silently overwrite another client's changes.
- `manual_links.json` and legacy `tags.json`: explicit identity links and any
  remaining historical tags or migration state.
- `settings.toml`: launch terminal and backend, since they affect session
  behavior independently of which client is open.
- External session facts used in the model: AgentsView catalog/detail/messages,
  Claude peer registrations, repository identity, and X11 window identity.
- Starting and freezing terminals, and window actions. A launch is an explicit
  request with confirmation in the client; the service checks again for an
  already running session before acting.

The service publishes immutable snapshots and accepts named commands. Each
response includes the service version, protocol version, and service PID.
Snapshot revisions are scoped to one service PID. Clients must discard a saved
revision when the service PID changes. A service error is visible to the client;
it must never silently fall back to writing the same state locally.

## What a client may keep locally

The GUI may store theme, zoom, pane widths, selected navigation node, and other
presentation preferences in `appearance.json`. The CLI may store its own output
preferences. These settings do not change session identity, organization,
history, launch policy, or the state another client should see.

An explicitly supplied import or export file is client input/output, not the
canonical state. The service validates imported data before changing its own
files. Diagnostic logs may be written by whichever process emits them.

## Migration and verification

Existing files remain in `~/.config/sessionlaunch`; the service opens them and
performs schema migrations with backups. The GUI keeps its last snapshot if the
service is unavailable and does not create an empty `groups.json`. Revision
checks reject stale organization writes. The GUI reconnects after a service
restart and discards its inventory revision when the service PID changes.

The production GUI and CLI use the service. `SESSIONLAUNCH_DISABLE_SERVICE=1`
remains a development switch for local tests; it is not a second supported
production owner. An existing GUI process must be restarted after installing
this version to run the new client code.

Both clients call `session_launch.service_client.request`, which
adds their version and protocol number to every request. The GUI identifies
itself with `client="gui"`; CLI requests use `client="cli"`. The CLI's
`install`, `version`, and `describe` commands do not need session state.
An explicitly supplied history export can be input to the `sessions` command;
the service remains the owner of live state and mutations. `revive` asks the
service for a fresh AgentsView record and uses its launch command after CLI
confirmation.
