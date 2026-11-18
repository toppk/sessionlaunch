"""Focused synthetic /proc check for the PTY inventory."""

import os
import shutil
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import inventory


class InventoryTest(unittest.TestCase):
    def test_window_title_alone_does_not_rewrite_history(self):
        previous = {"session": [], "saved": [], "terminal": [
            {"key": "one", "comm": "bash", "window_title": "one"}]}
        current = {**previous, "terminal": [
            {"key": "one", "comm": "bash", "window_title": "two"}]}
        self.assertTrue(inventory.same_observations(current, previous))
        current["terminal"][0]["comm"] = "vim"
        self.assertFalse(inventory.same_observations(current, previous))

    def test_update_reuses_history_until_an_external_write(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.toml"
            original = {"session": [], "terminal": [], "saved": [{"id": "first"}]}
            inventory.write_inventory(original, path)
            inventory._previous_cache.pop(path, None)
            with (mock.patch.object(inventory, "scan", return_value=[]),
                  mock.patch.object(inventory, "read_previous",
                                    wraps=inventory.read_previous) as read):
                self.assertEqual(len(inventory.update(path)["saved"]), 1)
                self.assertEqual(len(inventory.update(path)["saved"]), 1)
                self.assertEqual(read.call_count, 0)
                # An outside edit invalidates both the memory and sidecar caches.
                path.write_text(path.read_text().replace('id = "first"',
                                                         'id = "external-second"'))
                self.assertEqual(inventory.update(path)["saved"][0]["id"],
                                 "external-second")
                self.assertEqual(read.call_count, 1)
            inventory._previous_cache.pop(path, None)

    def test_corrupt_inventory_cache_falls_back_to_toml(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.toml"
            inventory.write_inventory({"session": [], "terminal": [],
                                       "saved": [{"id": "frozen"}]}, path)
            inventory._cache_path(path).write_text("not JSON")
            with mock.patch.object(inventory, "read_previous",
                                   wraps=inventory.read_previous) as read:
                self.assertEqual(inventory.load_previous(path)["saved"][0]["id"],
                                 "frozen")
                self.assertEqual(read.call_count, 1)
            self.assertEqual(inventory.load_previous(path)["saved"][0]["id"],
                             "frozen")

    def test_cache_after_write_matches_toml_records(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.toml"
            inventory.write_inventory({"session": [{"id": "agent"}],
                                       "saved": [{"id": "frozen"}],
                                       "terminal": [{"key": "window", "state": "live",
                                                     "pty": "/dev/pts/5", "argv": ["bash"],
                                                     "processes": [{"pid": 42, "comm": "bash"}],
                                                     "command_history": [{"pid": 41,
                                                                          "comm": "vim",
                                                                          "argv": ["vim", "a"]}]}]},
                                      path)
            self.assertEqual(inventory.load_previous(path), inventory.read_previous(path))

    def test_inventory_replacement_and_cache_are_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.toml"
            path.write_text("old")
            path.chmod(0o644)
            inventory.write_inventory({"session": [], "saved": [], "terminal": []}, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(inventory._cache_path(path).stat().st_mode & 0o777, 0o600)

    def test_removed_manual_link_clears_history_without_mutating_cached_gui_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.toml"
            previous = {"session": [], "saved": [], "terminal": [
                {"key": "window", "state": "live", "pty": "/dev/pts/5",
                 "agent": "claude", "session_id": "b999622d-d0d5-587b-ab0f-af3d7cab1b4e",
                 "session_id_source": "manual", "comm": "claude", "argv": ["claude"]}]}
            inventory.write_inventory(previous, path)
            cached = inventory.read_previous(path)
            stat = path.stat()
            inventory._previous_cache[path] = ((stat.st_mtime_ns, stat.st_size), cached)
            try:
                with mock.patch.object(inventory.manual_links, "load", return_value={}), \
                     mock.patch.object(inventory, "scan", return_value=[]), \
                     mock.patch.object(inventory.os, "listdir", return_value=["5"]):
                    result = inventory.update(path)
                self.assertEqual(result["terminal"][0]["session_id"], "")
                self.assertEqual(result["terminal"][0]["session_id_source"], "")
                self.assertEqual(cached["terminal"][0]["session_id_source"], "manual")
                self.assertEqual(inventory.read_previous(path)["terminal"][0]["session_id"], "")
            finally:
                inventory._previous_cache.pop(path, None)

    def test_command_history_and_ninety_day_retention(self):
        now = datetime(2026, 10, 13, tzinfo=timezone.utc)
        command = {"key": "xterm-one", "state": "live", "pty": "/dev/pts/7",
                   "pid": 42, "comm": "journalctl", "argv": ["journalctl", "-fu", "demo"],
                   "cwd": "/repo", "agent": "", "processes": []}
        shell = {**command, "pid": 40, "comm": "bash", "argv": ["bash"]}
        first = inventory.reconcile([command], {"session": [], "terminal": [], "saved": []}, now)
        same = inventory.reconcile([command], first, now + timedelta(seconds=1))
        self.assertEqual(first, same)
        returned = inventory.reconcile([shell], same, now + timedelta(seconds=2))
        self.assertEqual(returned["terminal"][0]["command_history"][0]["argv"],
                         ["journalctl", "-fu", "demo"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.toml"
            inventory.write_inventory(returned, path)
            loaded = inventory.read_previous(path)
            self.assertEqual(loaded["terminal"][0]["command_history"][0]["cwd"], "/repo")
        closed = inventory.reconcile([], returned, now + timedelta(minutes=1))
        self.assertEqual(closed["terminal"][0]["state"], "dormant")
        self.assertTrue(closed["terminal"][0]["closed_at"])
        recent = inventory.reconcile([], closed, now + timedelta(days=89))
        self.assertEqual(len(recent["terminal"]), 1)
        expired = inventory.reconcile([], recent, now + timedelta(days=91))
        self.assertEqual(expired["terminal"], [])

    def test_explicitly_saved_commands_outlive_observed_records(self):
        now = datetime(2026, 10, 13, tzinfo=timezone.utc)
        old = (now - timedelta(days=91)).isoformat()
        previous = {"session": [{"id": "agent", "source": "process", "state": "dormant",
                                 "closed_at": old}],
                    "terminal": [{"key": "old", "state": "dormant", "closed_at": old}],
                    "saved": [{"id": "frozen", "source_terminal_key": "old"}]}
        result = inventory.reconcile([], previous, now)
        self.assertEqual(result["session"], [])
        self.assertEqual(result["terminal"], [])
        self.assertEqual(result["saved"][0]["id"], "frozen")

    def test_startup_child_is_foreground_even_when_bash_leads_same_group(self):
        shell = {"pid": 10, "ppid": 1, "pgid": 10, "tpgid": 10, "start_ticks": 100}
        child = {"pid": 11, "ppid": 10, "pgid": 10, "tpgid": 10, "start_ticks": 101}
        self.assertIs(inventory.foreground_process([shell, child]), child)

    def test_pty_owner_foreground_agent_and_dormant_transition(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            proc = root / "proc"
            pts = root / "pts"
            proc.mkdir()
            pts.mkdir()
            (pts / "7").touch()
            session_id = "e2f700a6-1bc4-5e6c-983b-15b4329e785c"

            def add_process(pid, comm, ppid, pgid, tty, tpgid, argv, cwd):
                directory = proc / str(pid)
                (directory / "fd").mkdir(parents=True)
                (directory / "fdinfo").mkdir()
                fields = ["S", str(ppid), str(pgid), "100", str(tty), str(tpgid)]
                fields.extend(["0"] * 13)
                fields.append(str(pid * 100))  # stat field 22: starttime
                (directory / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))
                (directory / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
                (directory / "environ").write_bytes(f"PWD={cwd}\0".encode())
                (directory / "status").write_text("Uid:\t1000\t1000\t1000\t1000\n")
                (directory / "cwd").symlink_to(cwd)
                return directory

            owner = add_process(100, "xterm", 1, 100, 0, -1, ["xterm"], "/home/user")
            (owner / "fd" / "9").symlink_to("/dev/ptmx")
            (owner / "fdinfo" / "9").write_text("pos: 0\ntty-index:\t7\n")
            tty = os.makedev(136, 7)
            add_process(101, "bash", 100, 101, tty, 102, ["bash"], "/repo")
            agent = add_process(102, "codex", 101, 102, tty, 102,
                                ["codex", "resume", session_id], "/repo")
            (agent / "environ").write_bytes(
                b"PWD=/repo\0SESSIONLAUNCH_UNIT=sessionlaunch-example.service\0")

            current = inventory.scan(proc, pts, include_windows=False)
            self.assertEqual(len(current), 1)
            terminal = current[0]
            self.assertEqual((terminal["pty"], terminal["terminal"], terminal["pid"],
                              terminal["pgid"], terminal["comm"], terminal["cwd"]),
                             ("/dev/pts/7", "xterm", 102, 102, "codex", "/repo"))
            self.assertEqual(terminal["session_id"], session_id)
            self.assertEqual(terminal["launch_unit"], "sessionlaunch-example.service")
            self.assertEqual((terminal["uid"], terminal["euid"]), (1000, 1000))
            self.assertEqual(len(terminal["processes"]), 2)

            shutil.rmtree(proc / "102")
            shell_only = inventory.scan(proc, pts, include_windows=False)
            self.assertEqual(shell_only[0]["comm"], "bash")
            self.assertEqual(shell_only[0]["agent"], "")

            # A background agent must not replace the shell as the host application.
            shutil.rmtree(proc / "101")
            add_process(101, "bash", 100, 101, tty, 101, ["bash"], "/repo")
            add_process(103, "codex", 101, 103, tty, 101,
                        ["codex", "resume", session_id], "/repo")
            background = inventory.scan(proc, pts, include_windows=False)[0]
            self.assertEqual(background["host_application"], "bash")
            self.assertEqual(background["agent"], "codex")

            saved = inventory.reconcile(current, {"session": [], "terminal": []})
            self.assertEqual(saved["session"][0]["state"], "live")
            self.assertEqual(saved["session"][0]["id"], session_id)
            output = root / "sessions.toml"
            self.assertTrue(inventory.write_inventory(saved, output))
            self.assertFalse(inventory.write_inventory(saved, output))
            with output.open("rb") as stream:
                parsed = tomllib.load(stream)
            self.assertEqual(len(parsed["terminal"][0]["process"]), 2)
            self.assertEqual(parsed["terminal"][0]["euid"], 1000)
            later = inventory.reconcile([], parsed)
            self.assertEqual(later["terminal"][0]["state"], "dormant")
            self.assertEqual(later["session"][0]["state"], "dormant")

    def test_imported_hotlist_sessions_are_removed(self):
        previous = {"session": [{"id": "old", "source": "hotlist", "state": "unlinked"},
                                {"id": "observed", "source": "process", "state": "live"}],
                    "terminal": [], "saved": []}
        result = inventory.reconcile([], previous)
        self.assertEqual([row["id"] for row in result["session"]], ["observed"])
        self.assertEqual(result["session"][0]["state"], "dormant")


if __name__ == "__main__":
    unittest.main()
