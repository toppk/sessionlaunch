# SessionLaunch session model

Design note, 2026-11-17. Domains, multiple group memberships, Standing,
lifecycle, and retirement are stored in `groups.json` schema version 2. Older files
are backed up before migration. Domain assignments use the same stable session keys
as annotations. No domain is assigned by inference.

## What a session is

A session is the thing a person wants to find again and, when appropriate, resume.
An agent conversation is identified by its provider and UUID. A frozen command has
its own saved ID. A terminal without either identity is an observed terminal record.
The terminal window, PTY, process, and Claude background host are temporary ways to
access a session. Their PIDs and presence change independently of the session.

An agent's display name is a mutable label. It may carry structured hints, but it
must never replace the UUID as the key for notes, organization, or messaging.

## Four independent descriptions

| Description | Meaning | Intended cardinality |
| --- | --- | --- |
| Domain | The broad area the session belongs to | One per session |
| Group | A curated collection, often a swarm or effort | Zero or more per session |
| Standing | A session kept available for ongoing conversation | Yes or no |
| Annotation | A one-line explanation of a session's role and work | One global note per session today |

Domains organize discovery; they are not access or privacy boundaries. An
unassigned queue can hold sessions while they are classified, but it is not a
domain. Domain is independent of repository path. No domain is required to have
an admin.

A group can contain sessions from several domains. A `release` effort might
draw on `software` and `operations` sessions. A session can
participate in more than one group. The existing group folders and their visible
sections are the starting UI for this. Group membership is not the same as ownership
or a reporting relationship. A standing session can join an effort
temporarily without losing its standing status or domain.

The existing F2 annotation is a single preserved line of free text. Known markers
are `r:role`, `o:/scope`, and `t:/task`; markers may repeat. They describe hats,
oversight scopes, and work scopes, and help sort rows within a group. An annotation
belongs to the stable session identity, so it remains through terminal closure and
agent rename. If a session has different roles in different efforts, a later model
may add a note on each group membership while retaining the global note.

## Discovery after a reboot

Running, idle, background, attached, and closed are observed presence states, not
importance rankings. After a reboot the user should be able to browse domains and
cross-domain groups, search names and annotations, inspect last messages and the
proposed resume command, and revive one session when ready. There is no implication
that every previously open terminal should relaunch automatically.

SessionLaunch's `sessions.toml` keeps recent terminal observations and explicitly
frozen commands. AgentsView supplies historical agent conversations and transcript
metadata. `groups.json` keeps manual group memberships, sections, order, global
annotations, optional direct domain assignments, Standing, lifecycle, and retirement.
An explicit terminal-to-agent link is stored separately in
`~/.config/sessionlaunch/manual_links.json`. It requires a user-confirmed UUID from
AgentsView with the same provider and working directory, and is bound to the
observed terminal identity plus agent PID and process start ticks. The inventory
records its source as `manual`. Linking transfers temporary terminal organization
to the agent key; unlinking returns transferred organization to the terminal key.

## Name-derived organization

A name such as `editor/maintainer member:software::release` may suggest a
membership. Repeated `member:` markers can suggest several groups. The source of
each membership must remain visible: manual or derived from the current name.
Renaming can update derived memberships but must not erase manual assignments or
annotations. The meaning of `::` should be settled before parsing names in the UI.

## Current prototype gap

The one-line annotation is global to the session, even when it has different roles
in different groups. Name-derived group hints are not parsed or shown. The UI shows
direct and group-inherited domains, but does not distinguish a name hint from a
manual membership because only manual memberships exist today.
