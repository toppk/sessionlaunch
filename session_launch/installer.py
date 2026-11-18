"""Install the packaged daemon as an idempotent user systemd service."""

import argparse
import subprocess
from importlib.resources import files
from pathlib import Path

from .sessionlaunch_paths import secure_config_dir


UNIT_NAME = "sessionlaunch.service"


def install(executable=None, unit_path=None):
    executable = Path(executable or Path.home() / ".local/bin/sessionlaunch")
    if not executable.is_file():
        raise FileNotFoundError(f"Install the SessionLaunch wheel first: {executable}")
    secure_config_dir()
    destination = Path(unit_path or Path.home() / ".config/systemd/user" / UNIT_NAME)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    template = files("session_launch").joinpath("sessionlaunch.service.in").read_text()
    contents = template.replace("@EXECUTABLE@", str(executable))
    destination.write_text(contents)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", UNIT_NAME], check=True)
    subprocess.run(["systemctl", "--user", "restart", UNIT_NAME], check=True)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        destination = install()
    except (OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"sessionlaunch install: {error}\n")
    print(f"Installed and started {destination}")
    return 0
