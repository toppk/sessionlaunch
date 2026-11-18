"""Service protocol tests use disposable files and never launch an agent."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from sessionlaunch_service import InventoryState, ServiceServer, dispatch
from sessionlaunch_service_client import request


class ServiceTest(unittest.TestCase):
    def test_socket_snapshot_and_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.socket"
            state = InventoryState(Path(directory) / "sessions.toml")
            state.data = {"session": [], "saved": [], "terminal": [
                {"key": "pty:42", "state": "live", "window_id": "0x123"}]}
            state.revision = 3
            server = ServiceServer(path, state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(request("status", path=path)["revision"], 3)
                self.assertEqual(request("snapshot", path=path)["data"], state.data)
                self.assertTrue(request("snapshot", path=path, since=3)["unchanged"])
                with patch("sessionlaunch_service.subprocess.run") as run:
                    self.assertEqual(request("raise", path=path,
                                             terminal_key="pty:42")["window_id"], "0x123")
                    self.assertEqual(run.call_args_list[-1].args[0],
                                     ["wmctrl", "-ia", "0x123"])
                with self.assertRaisesRegex(ValueError, "No X11 window"):
                    request("raise", path=path, terminal_key="missing")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_scan_and_freeze_share_one_inventory_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.toml"
            live = {"session": [], "saved": [], "terminal": [
                {"key": "pty:42", "state": "live", "argv": ["vim", "todo.txt"],
                 "cwd": directory}]}
            state = InventoryState(path)
            with patch("sessionlaunch_service.inventory.update", return_value={
                    **live, "changed": True, "scan_status": "ok"}) as update, \
                 patch("sessionlaunch_service.restore.freeze_terminal",
                       return_value={"id": "saved-one"}) as freeze:
                state.scan_once()
                self.assertEqual(state.revision, 1)
                saved = dispatch(state, {"action": "freeze", "terminal_key": "pty:42"})
                self.assertEqual(saved["saved"]["id"], "saved-one")
                self.assertEqual(state.revision, 2)
                self.assertEqual(update.call_count, 2)
                freeze.assert_called_once_with(live["terminal"][0], path)


if __name__ == "__main__":
    unittest.main()
