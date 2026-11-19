"""Deployment points systemd at the installed command, not the checkout."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session_launch import installer


class InstallerTest(unittest.TestCase):
    def test_unit_uses_installed_command_and_prepares_private_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "bin" / "sessionlaunch"
            executable.parent.mkdir()
            executable.write_text("#!/usr/bin/python3\n")
            unit = root / "systemd" / "sessionlaunch.service"
            with patch.object(installer, "secure_config_dir") as secure, \
                 patch.object(installer.subprocess, "run") as run:
                self.assertEqual(installer.install(executable, unit), unit)
            secure.assert_called_once_with()
            self.assertIn(f"ExecStart={executable} daemon", unit.read_text())
            self.assertNotIn("workspace/sessionlaunch", unit.read_text())
            self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                             ["daemon-reload", "sessionlaunch.service", "sessionlaunch.service"])


if __name__ == "__main__":
    unittest.main()
