"""Read-only queries over an inventory containing historical terminal commands."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from session_launch import inventory
from session_launch import sessions_cli as sessionlaunch_cli


class SessionLaunchCliTest(unittest.TestCase):
    def test_search_history_sort_and_show(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.toml"
            data = {"session": [], "saved": [], "terminal": [
                {"key": "first-terminal", "state": "live", "pty": "/dev/pts/3",
                 "terminal": "xterm", "terminal_pid": 100, "pid": 101,
                 "comm": "bash", "argv": ["bash"], "cwd": "/home/user",
                 "agent": "", "command_history": [
                     {"pid": 102, "comm": "journalctl", "argv": ["journalctl", "-fu", "demo"],
                      "cwd": "/home/user", "ended_at": "2026-10-12T10:00:00+00:00"}]},
                {"key": "second-terminal", "state": "dormant", "pty": "/dev/pts/4",
                 "terminal": "foot", "terminal_pid": 200, "pid": 201,
                 "comm": "vim", "argv": ["vim", "/tmp/file"], "cwd": "/repo",
                 "agent": "", "closed_at": "2026-10-11T10:00:00+00:00"},
            ]}
            inventory.write_inventory(data, path)
            before = path.read_bytes()

            def query(*options):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(sessionlaunch_cli.main(["--file", str(path), *options]), 0)
                return json.loads(output.getvalue())

            found = query("list", "--state", "live", "--search", "journalctl", "--json")
            self.assertEqual([row["id"] for row in found], ["first-terminal"])
            commands = query("history", "--search", "journalctl", "--json")
            self.assertEqual(commands[0]["phase"], "previous")
            self.assertEqual(commands[0]["command"], "journalctl -fu demo")
            sorted_terminals = query("list", "--sort", "directory", "--json")
            self.assertEqual([row["id"] for row in sorted_terminals],
                             ["first-terminal", "second-terminal"])
            exact = query("show", "terminal", "second")
            self.assertEqual(exact["terminal_pid"], 200)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
