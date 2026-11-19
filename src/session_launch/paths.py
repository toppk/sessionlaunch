"""Shared SessionLaunch data and runtime paths."""

import os
import tempfile
from pathlib import Path


CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sessionlaunch"
RUNTIME_DIR = (Path(os.environ["XDG_RUNTIME_DIR"]) / "sessionlaunch"
               if os.environ.get("XDG_RUNTIME_DIR") else CONFIG_DIR)
SERVICE_SOCKET = RUNTIME_DIR / "service.socket"
SESSIONS_FILE = CONFIG_DIR / "sessions.toml"
_package_parent = Path(__file__).resolve().parents[1]
# Older checkout builds stored this file at the repository root. Installed
# packages still use the same parent they used before the source layout move.
LEGACY_SESSIONS_FILE = ((_package_parent.parent if _package_parent.name == "src"
                         else _package_parent) / "sessions.toml")


def secure_config_dir(path=CONFIG_DIR):
    """Keep existing SessionLaunch data private, including older loose files."""
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise PermissionError(f"Config directory is not owned by this user: {path}")
    path.chmod(0o700)
    for root, directories, files in os.walk(path):
        for name in directories:
            entry = Path(root) / name
            if not entry.is_symlink():
                entry.chmod(0o700)
        for name in files:
            entry = Path(root) / name
            if not entry.is_symlink():
                entry.chmod(0o600)


def private_write_text(path, contents):
    """Atomically replace a text file with one readable only by its owner."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
