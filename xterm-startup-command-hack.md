# Xterm: one-shot startup command with a separate Bash rcfile

## Goal

Open Xterm in a chosen directory, run a startup command (such as `vim README.md`), and return to an ordinary interactive Bash prompt when it exits. **Do not modify `~/.bashrc`**, inject keystrokes, or use X11 `SendEvent`.

## Separate rcfile

Create `~/.config/xterm/startup.bashrc`:

```bash
# Normal interactive shell setup first.
if [[ -f ~/.bashrc ]]; then
    source ~/.bashrc
fi

# A one-shot command supplied by this launcher only.
if [[ -n ${XTERM_STARTUP_COMMAND-} ]]; then
    _startup_command=$XTERM_STARTUP_COMMAND
    unset XTERM_STARTUP_COMMAND
    eval "$_startup_command"
    unset _startup_command
fi
```

The normal `.bashrc` is sourced, not edited. The environment variable is cleared before execution so it cannot accidentally trigger in a child shell.

## Launch

```bash
(
    cd -- "$HOME/projects/example" || exit
    export XTERM_STARTUP_COMMAND='vim README.md'
    exec xterm -e bash --rcfile "$HOME/.config/xterm/startup.bashrc" -i
)
```

The subshell isolates the directory and environment changes from the caller. Xterm runs Bash, **not Vim**; Bash runs Vim during initialization, then continues to its interactive prompt after Vim exits. Unlike `xterm -e vim`, the window stays open.

## Security and scope

This is an opt-in Bash startup mechanism, not a standard terminal protocol. The special rcfile is used only by the launcher. `eval` executes arbitrary Bash code, so the startup command must come from a trusted source. For untrusted command arguments or filenames, pass them as positional parameters or via a safely quoted argv representation rather than interpolating them into a command string. The environment variable can also be visible to same-user processes while the shell starts; do not put secrets in it.

**Note:** `--rcfile` is used for an interactive, non-login Bash. If the normal `.bashrc` calls `exit`, or uses unusual startup control flow, it may prevent the extra command from running. If the requested startup command exits the shell itself, the window will close.

## Why this approach

No changes to global shell startup behavior, no X11 permissions window, no timing-dependent injection, and no Bash patches. It solves the startup-only case; it does not implement the richer bidirectional terminal/application protocol.
