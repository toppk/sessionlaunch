"""Run the real PTY capture/revive cycle with a disposable xterm under Xvfb.

    SESSIONLAUNCH_INTEGRATION=1 xvfb-run -a python3 -m unittest -v test_restore_cycle
"""

import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import inventory
import restore
from sessionlaunch import RC_TEXT


def until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.15)
    raise AssertionError("Timed out waiting for terminal observation")


class RestoreCycleTest(unittest.TestCase):
    def test_settings_and_command_guard(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sessionlaunch" / "settings.toml"
            self.assertEqual(restore.read_settings(path)["default_terminal"], "xterm")
            for name in restore.TERMINALS:
                restore.write_settings(path, name)
                self.assertEqual(restore.read_settings(path)["default_terminal"], name)
            with self.assertRaises(ValueError):
                restore.frozen_command({"replay_policy": "preview", "command_argv": ["claude"]})

    def test_observed_shell_comes_from_attached_parent(self):
        terminal = {"pid": 12, "processes": [
            {"pid": 12, "ppid": 11, "comm": "python3", "argv": ["python3", "script.py"]},
            {"pid": 11, "ppid": 1, "comm": "bash", "argv": ["/usr/bin/bash"]},
        ]}
        shell, source = restore.observed_shell(terminal)
        self.assertEqual(Path(shell).resolve(), Path("/usr/bin/bash").resolve())
        self.assertEqual(source, "observed")

    @unittest.skipUnless(os.environ.get("SESSIONLAUNCH_INTEGRATION") == "1",
                         "requires Xvfb and a disposable xterm")
    def test_observe_freeze_close_revive(self):
        self.assertTrue(os.environ.get("DISPLAY"), "Run under xvfb-run")
        children = []
        try:
            with tempfile.TemporaryDirectory(prefix="sessionlaunch-cycle-") as root:
                root = Path(root)
                marker = root / "runs.txt"
                script = root / "harmless.py"
                script.write_text("import pathlib, sys, time\n"
                                  "with pathlib.Path(sys.argv[1]).open('a') as out:\n"
                                  "    out.write('run\\n')\n"
                                  "time.sleep(30)\n")
                inventory_path = root / "sessions.toml"
                settings_path = root / "config" / "sessionlaunch" / "settings.toml"
                rcfile = root / "startup.bashrc"
                rcfile.write_text(RC_TEXT)
                restore.write_settings(settings_path, "xterm")
                original = subprocess.Popen(
                    ["xterm", "-title", "sessionlaunch capture fixture", "-e",
                     sys.executable, str(script), str(marker)],
                    cwd=root, start_new_session=True)
                children.append(original)

                def observed_original():
                    return next((row for row in inventory.scan(include_windows=False)
                                 if str(script) in row.get("argv", []) and row["state"] == "live"), None)

                terminal = until(observed_original)
                self.assertEqual(marker.read_text(), "run\n")
                saved = restore.freeze_terminal(terminal, inventory_path)
                self.assertEqual(saved["replay_policy"], "run")
                self.assertEqual(saved["command_argv"], terminal["argv"])
                self.assertEqual(saved["shell_source"], "default")
                linked_source = inventory.reconcile([terminal], inventory.read_previous(inventory_path),
                                                    root / "absent-hotlist.toml")
                self.assertEqual(linked_source["saved"][0]["state"], "live")
                original.terminate()
                original.wait(timeout=5)
                until(lambda: not observed_original())
                closed_source = inventory.reconcile([], linked_source,
                                                    root / "absent-hotlist.toml")
                self.assertEqual(closed_source["saved"][0]["state"], "saved")

                loaded = inventory.read_previous(inventory_path)["saved"][0]
                self.assertEqual(loaded["shell"], saved["shell"])
                command = restore.frozen_command(loaded)
                revived = restore.launch(restore.read_settings(settings_path)["default_terminal"],
                                         loaded["directory"], "sessionlaunch revival fixture",
                                         command, rcfile, loaded["id"], backend="direct")
                children.append(revived)
                until(lambda: marker.exists() and marker.read_text().count("run\n") == 2)

                def observed_revived():
                    return next((row for row in inventory.scan(include_windows=False)
                                 if row.get("frozen_id") == loaded["id"]), None)

                linked = until(observed_revived)
                self.assertEqual(linked["pty"].startswith("/dev/pts/"), True)
                self.assertEqual(linked["argv"], saved["command_argv"], linked["processes"])
                data = inventory.reconcile([linked], inventory.read_previous(inventory_path),
                                           root / "absent-hotlist.toml")
                self.assertEqual(data["saved"][0]["state"], "live")
                revived.terminate()
                revived.wait(timeout=5)
                until(lambda: not observed_revived())
                data = inventory.reconcile([], data)
                self.assertEqual(data["saved"][0]["state"], "saved")
                self.assertEqual(marker.read_text(), "run\nrun\n")
        finally:
            for child in children:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=5)

    @unittest.skipUnless(os.environ.get("SESSIONLAUNCH_INTEGRATION") == "1",
                         "requires Xvfb and a disposable xterm")
    def test_shell_handoff_after_one_shot_command(self):
        for shell in ("/usr/bin/bash", "/usr/bin/sh"):
            with self.subTest(shell=shell), tempfile.TemporaryDirectory(
                    prefix="sessionlaunch-shell-") as root:
                root = Path(root)
                marker = root / "ran.txt"
                rcfile = root / "startup.bashrc"
                rcfile.write_text(RC_TEXT)
                frozen_id = "shell-handoff-" + Path(shell).name
                process = restore.launch("xterm", root, "sessionlaunch shell fixture",
                                         f"printf '%s\\n' ok > {shlex.quote(str(marker))}",
                                         rcfile, frozen_id, shell, backend="direct")
                try:
                    until(lambda: marker.exists() and marker.read_text() == "ok\n")

                    def attached_shell():
                        return next((row for row in inventory.scan(include_windows=False)
                                     if row.get("frozen_id") == frozen_id and
                                     Path(row.get("argv", [""])[0]).name == Path(shell).name), None)

                    attached = until(attached_shell)
                    self.assertNotIn("--rcfile", attached["argv"])
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=5)

    @unittest.skipUnless(os.environ.get("SESSIONLAUNCH_INTEGRATION") == "1",
                         "requires Xvfb and a disposable xterm")
    def test_xterm_windowid_survives_without_launcher_state(self):
        process = subprocess.Popen(["xterm", "-e", "sleep", "5"], start_new_session=True)
        try:
            def observed_window():
                return next((row for row in inventory.scan()
                             if row["terminal_pid"] == process.pid and row["window_id"]), None)

            terminal = until(observed_window)
            self.assertEqual(terminal["window_source"], "WINDOWID")
            self.assertTrue(terminal["window_id"].startswith("0x"))
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
