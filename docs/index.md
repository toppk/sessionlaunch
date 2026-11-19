---
title: Overview
hero: true
hero-eyebrow: SessionLaunch · Linux desktop prototype
hero-title: Find your way back to a session.
hero-lede: See what is running, keep the conversations that matter, and review a resume command before opening a new terminal.
hero-image: assets/logo-512.webp
hero-links:
  - { label: Get started, href: getting-started.html, kind: primary }
  - { label: Understand the model, href: model.html, kind: secondary }
description: SessionLaunch organizes terminal and agent sessions on a Linux desktop.
---

# SessionLaunch

::: cards
::: card
[Observe]{.label}

### Find the live terminal

SessionLaunch inventories local PTYs and shows the foreground program, working
directory, process details, and a verified X11 window when one is available.
:::

::: card
[Organize]{.label}

### Keep the conversation

Domains, groups, sections, annotations, and a Standing shelf make agent
conversations easier to find after terminals close or the desktop restarts.
:::

::: card
[Return]{.label}

### Review before revival

See the proposed directory and command, then confirm each agent revival. A
reboot checklist can gather several conversations, with a final confirmation.
:::
:::

## What this prototype covers

SessionLaunch runs on a Linux desktop with Python, PySide6, and a per-user
service. Its X11 controls can raise, lower, and minimize windows when
the terminal exposes a verifiable window ID. It can identify Claude Code and
Codex sessions through their durable IDs and AgentsView metadata.

SessionLaunch does not restore screen contents, unsaved state, or window
geometry. A PTY scan can miss a command that starts and exits between polls.
Wayland activation depends on the compositor and terminal. See the
[getting started guide](getting-started.html) for installation and operation.

::: note
Session histories can contain commands, directories, and conversation IDs.
SessionLaunch stores its inventory in a private user configuration directory;
the documentation site contains no session data.
:::
