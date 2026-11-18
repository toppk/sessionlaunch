"""Derive a stable display tag from the nearest local Git repository."""

import configparser
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


def origin_path(url):
    """Return the owner/repository portion of a network Git URL, if present."""
    url = url.strip()
    if "://" in url:
        parsed = urlparse(url)
        if not parsed.hostname:
            return None
        path = parsed.path
    else:
        scp = re.match(r"^(?:[^@/:]+@)?[^@/:]+:(.+)$", url)
        if not scp:
            return None
        path = scp.group(1)
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    parts[-1] = re.sub(r"\.git$", "", parts[-1], flags=re.I)
    return "/".join(parts) if parts[-1] else None


def git_config(directory):
    dotgit = directory / ".git"
    if dotgit.is_dir():
        return dotgit / "config"
    if dotgit.is_file():
        line = dotgit.read_text(errors="replace").strip()
        if line.startswith("gitdir:"):
            gitdir = (directory / line[7:].strip()).resolve()
            common = gitdir / "commondir"
            if common.is_file():
                return (gitdir / common.read_text().strip()).resolve() / "config"
            return gitdir / "config"
    return None


@lru_cache(maxsize=1024)
def repo_tag_for(path):
    if not path:
        return None
    current = Path(path).expanduser().resolve()
    for directory in (current, *current.parents):
        try:
            config_path = git_config(directory)
            if config_path is None:
                continue
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(config_path)
            origin = parser.get('remote "origin"', "url", fallback="")
            name = origin_path(origin) if origin else None
            return f"repo:{name}" if name else f"repo:local/{directory.name}"
        except (OSError, configparser.Error):
            return f"repo:local/{directory.name}"
    return None
