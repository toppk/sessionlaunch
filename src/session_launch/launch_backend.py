"""Small, replaceable process launch backends for SessionLaunch."""

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field


BACKENDS = ("systemd-user", "direct")
SYSTEMD_RUN = "/usr/bin/systemd-run"
GRAPHICAL_ENV = (
    "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
    "PATH", "HOME", "SHELL", "XDG_CONFIG_HOME",
)
STARTUP_ENV = (
    "SESSIONLAUNCH_STARTUP_COMMAND", "SESSIONLAUNCH_WINDOW_TITLE",
    "SESSIONLAUNCH_RETURN_SHELL", "SESSIONLAUNCH_FROZEN_ID", "SESSIONLAUNCH_UNIT",
)


@dataclass
class SystemdHandle:
    unit: str
    _checked_at: float = field(default_factory=time.monotonic)
    _result: int | None = None

    def poll(self):
        if self._result is not None:
            return self._result
        if time.monotonic() - self._checked_at < 2:
            return None
        self._checked_at = time.monotonic()
        try:
            result = subprocess.run(["systemctl", "--user", "is-active", "--quiet", self.unit],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=3, check=False)
            if result.returncode != 0:
                self._result = result.returncode
        except (OSError, subprocess.TimeoutExpired):
            self._result = 1
        return self._result


def launch_direct(argv, directory, env):
    return subprocess.Popen(argv, cwd=directory, env=env, start_new_session=True)


def systemd_command(argv, directory, env, unit):
    command = [
        SYSTEMD_RUN, "--user", "--collect", "--quiet", f"--unit={unit}",
        "--property=Type=exec", "--property=TimeoutStopSec=10min",
        "--property=StandardOutput=journal", "--property=StandardError=journal",
        f"--working-directory={directory}", "--expand-environment=no",
    ]
    for key in (*GRAPHICAL_ENV, *STARTUP_ENV):
        if key in env:
            command.append(f"--setenv={key}={env[key]}")
    return [*command, "--", *argv]


def launch_systemd_user(argv, directory, env):
    if not os.path.isfile(SYSTEMD_RUN):
        raise FileNotFoundError(f"systemd-run is unavailable: {SYSTEMD_RUN}")
    if not shutil.which("systemctl"):
        raise FileNotFoundError("systemctl is unavailable")
    unit = f"sessionlaunch-{uuid.uuid4().hex}.service"
    env = {**env, "SESSIONLAUNCH_UNIT": unit}
    result = subprocess.run(systemd_command(argv, directory, env, unit),
                            cwd=directory, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise OSError(f"Could not start {unit}: {detail}")
    return SystemdHandle(unit)


def launch(backend, argv, directory, env):
    if backend == "systemd-user":
        return launch_systemd_user(argv, directory, env)
    if backend == "direct":
        return launch_direct(argv, directory, env)
    raise ValueError(f"Unsupported launch backend: {backend}")
