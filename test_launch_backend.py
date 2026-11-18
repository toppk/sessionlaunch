"""Check the transient service launch plan without opening a terminal."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import launch_backend
import restore


class LaunchBackendTest(unittest.TestCase):
    def test_settings_default_and_direct_override_survive_terminal_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.toml"
            self.assertEqual(restore.read_settings(path)["launch_backend"], "systemd-user")
            path.write_text('default_terminal = "foot"\nlaunch_backend = "direct"\n')
            restore.write_settings(path, "xterm")
            self.assertEqual(restore.read_settings(path),
                             {"default_terminal": "xterm", "launch_backend": "direct"})

    def test_user_service_launch_and_failure_diagnostics(self):
        env = {"DISPLAY": ":1", "HOME": "/home/user",
               "SESSIONLAUNCH_STARTUP_COMMAND": "printf '%s\\n' harmless"}
        argv = ["/usr/bin/xterm", "-e", "bash"]
        with patch.object(launch_backend.os.path, "isfile", return_value=True), \
             patch.object(launch_backend.shutil, "which", return_value="/usr/bin/systemctl"), \
             patch.object(launch_backend.uuid, "uuid4") as uuid4, \
             patch.object(launch_backend.subprocess, "run") as run:
            uuid4.return_value.hex = "abcd1234"
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            handle = launch_backend.launch_systemd_user(argv, "/repo", env)
            self.assertEqual(handle.unit, "sessionlaunch-abcd1234.service")
            command = run.call_args.args[0]
            self.assertEqual(command[:5], ["/usr/bin/systemd-run", "--user", "--collect",
                                           "--quiet", "--unit=sessionlaunch-abcd1234.service"])
            self.assertIn("--property=Type=exec", command)
            self.assertIn("--property=TimeoutStopSec=10min", command)
            self.assertIn("--property=StandardOutput=journal", command)
            self.assertIn("--property=StandardError=journal", command)
            self.assertIn("--working-directory=/repo", command)
            self.assertIn("--setenv=DISPLAY=:1", command)
            self.assertIn("--setenv=SESSIONLAUNCH_UNIT=sessionlaunch-abcd1234.service", command)
            self.assertEqual(command[-4:], ["--", "/usr/bin/xterm", "-e", "bash"])
            run.return_value = subprocess.CompletedProcess([], 1, "", "user bus unavailable")
            with self.assertRaisesRegex(OSError, "user bus unavailable"):
                launch_backend.launch_systemd_user(argv, "/repo", env)


if __name__ == "__main__":
    unittest.main()
