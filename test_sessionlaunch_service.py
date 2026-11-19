"""Service protocol tests use disposable files and never launch an agent."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from session_launch import __version__
from session_launch.session_groups import SessionGroups
from session_launch.service_protocol import PROTOCOL_VERSION
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
                status = request("status", path=path)
                self.assertEqual(status["revision"], 3)
                self.assertEqual(status["version"], __version__)
                self.assertEqual(status["service_version"], __version__)
                self.assertEqual(status["protocol_version"], PROTOCOL_VERSION)
                self.assertEqual(request("snapshot", path=path)["data"], state.data)
                self.assertTrue(request("snapshot", path=path, since=3)["unchanged"])
                request("snapshot", path=path, client="gui")
                self.assertEqual(sum(row[0] == "cli" for row in server.seen_clients), 1)
                self.assertTrue(any(row[0] == "gui" and row[2:] ==
                                    (__version__, PROTOCOL_VERSION)
                                    for row in server.seen_clients))
                with patch("sessionlaunch_service.subprocess.run") as run:
                    self.assertEqual(request("raise", path=path,
                                             terminal_key="pty:42")["window_id"], "0x123")
                    self.assertEqual(run.call_args_list[-1].args[0],
                                     ["wmctrl", "-ia", "0x123"])
                    run.return_value.stdout = "0x123 0 42 host.class localhost sessionlaunch-abc\n"
                    self.assertEqual(request("window_find", path=path,
                                             token="sessionlaunch-abc")["window_id"], "0x123")
                    request("window_action", path=path, window_id="0x123",
                            operation="maximize")
                    self.assertEqual(run.call_args_list[-1].args[0][:3],
                                     ["wmctrl", "-ir", "0x123"])
                with self.assertRaisesRegex(ValueError, "Invalid X11 window ID"):
                    request("window_action", path=path, window_id="not-a-window",
                            operation="raise")
                with self.assertRaisesRegex(ValueError, "No X11 window"):
                    request("raise", path=path, terminal_key="missing")
                revive = {"agent": "codex",
                          "id": "4e5da2ba-0d5e-5c4d-a697-94d3a534ff4a",
                          "cwd": directory, "title": "Review"}
                self.assertEqual(request("revive_queue", path=path,
                                         sessions=[revive])["queued"], 1)
                self.assertEqual(request("revive_take", path=path)["sessions"], [revive])
                self.assertEqual(request("revive_take", path=path)["sessions"], [])
                with self.assertRaisesRegex(ValueError, "Invalid agent"):
                    request("revive_queue", path=path,
                            sessions=[{**revive, "id": "not-a-uuid"}])
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

    def test_organization_revision_prevents_lost_keep_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InventoryState(Path(directory) / "sessions.toml")
            initial = state.organization()
            revision = initial["revision"]
            document = {**initial["data"],
                        "retired": {"agent:claude:one": "2026-11-18T00:00:00+00:00"}}
            saved = state.save_organization(document, revision)
            self.assertIn("agent:claude:one", state.organization()["data"]["retired"])
            with self.assertRaisesRegex(ValueError, "changed in another client"):
                state.save_organization({**document, "retired": {}}, revision)
            self.assertTrue(state.organization(saved["revision"])["unchanged"])

    def test_repo_tag_uses_a_field_distinct_from_socket_path(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InventoryState(Path(directory) / "sessions.toml")
            with patch("sessionlaunch_service.repo_tags.repo_tag_for",
                       return_value="repo:example/project") as lookup:
                answer = dispatch(state, {"action": "repo_tag", "repo_path": directory})
            self.assertEqual(answer["tag"], "repo:example/project")
            lookup.assert_called_once_with(directory)

    def test_launch_rejects_unlinked_same_folder_agent_without_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InventoryState(Path(directory) / "sessions.toml")
            live = {"session": [], "saved": [], "terminal": [{
                "key": "pty:7", "state": "live", "agent": "claude",
                "session_id": "", "agent_cwd": directory, "pty": "/dev/pts/7"}]}
            session_id = "4e5da2ba-0d5e-5c4d-a697-94d3a534ff4a"
            with patch("sessionlaunch_service.inventory.update", return_value=live), \
                 patch("sessionlaunch_service.restore.launch") as launch:
                with self.assertRaisesRegex(ValueError, "running unlinked"):
                    state.launch_terminal({"terminal": "xterm", "directory": directory,
                                           "title": "Review", "command": "printf preview",
                                           "identity": f"agent:claude:{session_id}"})
                launch.assert_not_called()

    def test_launch_rejects_running_claude_peer_without_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InventoryState(Path(directory) / "sessions.toml")
            session_id = "4e5da2ba-0d5e-5c4d-a697-94d3a534ff4a"
            with patch("sessionlaunch_service.inventory.update", return_value={
                    "session": [], "saved": [], "terminal": []}), \
                 patch("sessionlaunch_service.claude_peers.read_peers", return_value={
                    session_id: {"name": "already-running"}}), \
                 patch("sessionlaunch_service.restore.launch") as launch:
                with self.assertRaisesRegex(ValueError, "already running as a peer"):
                    state.launch_terminal({"terminal": "xterm", "directory": directory,
                                           "title": "Review", "command": "printf preview",
                                           "identity": f"agent:claude:{session_id}"})
                launch.assert_not_called()

    def test_manual_link_moves_organization_and_unlink_restores_it(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InventoryState(Path(directory) / "sessions.toml")
            groups = SessionGroups(state.groups_path)
            group_id = groups.create("review")
            groups.assign(["terminal:window"], group_id)
            groups.set_annotation(["terminal:window"], "r:reviewer")
            session_id = "4e5da2ba-0d5e-5c4d-a697-94d3a534ff4a"
            terminal = {"key": "window", "state": "live", "agent": "codex",
                        "agent_pid": 42, "agent_start_ticks": 100,
                        "agent_cwd": directory, "cwd": directory, "session_id": ""}
            fresh = {"session": [], "saved": [], "terminal": [terminal]}
            request_data = {"terminal_key": "window", "provider": "codex",
                            "session_id": session_id, "agent_pid": 42,
                            "agent_start_ticks": 100, "cwd": directory}
            with patch("sessionlaunch_service.inventory.update", return_value=fresh), \
                 patch("sessionlaunch_service.agent_link.verify_session", return_value={}):
                state.change_manual_link(request_data)
                canonical = f"agent:codex:{session_id}"
                linked = SessionGroups(state.groups_path)
                self.assertEqual(linked.memberships[canonical][0]["group"], group_id)
                self.assertEqual(linked.annotations[canonical], "r:reviewer")
                self.assertNotIn("terminal:window", linked.memberships)
                state.change_manual_link({"terminal_key": "window"}, unlink=True)
                restored = SessionGroups(state.groups_path)
                self.assertEqual(restored.memberships["terminal:window"][0]["group"], group_id)
                self.assertEqual(restored.annotations["terminal:window"], "r:reviewer")


if __name__ == "__main__":
    unittest.main()
