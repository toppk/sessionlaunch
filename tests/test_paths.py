"""Private config storage stays private across upgrades and replacements."""

import tempfile
import unittest
from pathlib import Path

from session_launch.paths import private_write_text, secure_config_dir


class PrivateStorageTest(unittest.TestCase):
    def test_tightens_existing_storage_and_keeps_replacements_private(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "sessionlaunch"
            config.mkdir(mode=0o755)
            existing = config / "sessions.toml"
            existing.write_text("history")
            existing.chmod(0o644)
            secure_config_dir(config)
            self.assertEqual(config.stat().st_mode & 0o777, 0o700)
            self.assertEqual(existing.stat().st_mode & 0o777, 0o600)
            private_write_text(existing, "new history")
            self.assertEqual(existing.read_text(), "new history")
            self.assertEqual(existing.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
