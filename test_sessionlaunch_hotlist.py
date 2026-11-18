"""Read-only hotlist matching tests; no agent executable is invoked."""

import io
import json
import errno
import os
import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

import hotlist_ipc
from hotlist_server import HotlistServer, validate_request
import sessionlaunch_hotlist as hotlist


class HotlistTest(unittest.TestCase):
    def test_match_exact_session_and_mark_unresolved_repo(self):
        rows = [
            {"agent": "claude", "id": "abc", "repo": "/tmp/work"},
            {"agent": "claude", "id": "def", "repo": "/tmp/work"},
            {"agent": "codex", "id": "ghi", "repo": "/tmp/other"},
        ]
        processes = [
            {"agent": "claude", "id": "abc", "source": "argv", "pid": 11,
             "pty": "/dev/pts/3", "cwd": "/tmp/work"},
            {"agent": "claude", "id": "", "source": "", "pid": 12,
             "pty": "/dev/pts/4", "cwd": "/tmp/work/subdir"},
        ]
        result = hotlist.classify(rows, processes)
        self.assertEqual([row["status"] for row in result],
                         ["running", "unverified", "stopped"])
        self.assertEqual(result[0]["processes"][0]["pid"], 11)
        self.assertEqual(result[1]["processes"][0]["pid"], 12)
        self.assertFalse(hotlist.repo_contains("/tmp/work", "/tmp/work-other"))

    def test_cli_json_uses_hotlist_without_launching_or_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hotlist.toml"
            original = ('[[session]]\nagent = "claude"\nid = "abc"\n'
                        'repo = "/tmp/work"\ntitle = "Example"\n')
            path.write_text(original)
            output = io.StringIO()
            with patch.object(hotlist, "live_agents", return_value=[]), redirect_stdout(output):
                self.assertEqual(hotlist.main(["--hotlist", str(path), "--json"]), 0)
            self.assertEqual(json.loads(output.getvalue())[0]["status"], "stopped")
            self.assertEqual(path.read_text(), original)

    def test_revive_sends_only_stopped_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hotlist.toml"
            path.write_text('[[session]]\nagent = "claude"\nid = "aaa"\n'
                            'repo = "/tmp/example-project"\ntitle = "Stopped"\n'
                            '[[session]]\nagent = "claude"\nid = "bbb"\n'
                            'repo = "/tmp/example-project"\ntitle = "Running"\n')
            running = [{"agent": "claude", "id": "bbb", "source": "argv", "pid": 10,
                        "pty": "/dev/pts/1", "cwd": "/tmp/example-project"}]
            output = io.StringIO()
            with patch.object(hotlist, "live_agents", return_value=running), \
                 patch.object(hotlist_ipc, "request_revive", return_value=1) as send, \
                 redirect_stdout(output):
                self.assertEqual(hotlist.main(["--hotlist", str(path), "--search", "example-project"],
                                              action="request-revive"), 0)
            self.assertEqual([row["id"] for row in send.call_args.args[0]], ["aaa"])
            self.assertEqual(path.read_text().count("[[session]]"), 2)

    def test_exact_id_revive_selects_one_even_in_same_repo(self):
        first = "4e5da2ba-0d5e-5c4d-a697-94d3a534ff4a"
        second = "aa403547-b91b-504a-ac6f-0de875954184"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hotlist.toml"
            path.write_text(f'[[session]]\nagent = "codex"\nid = "{first}"\n'
                            'repo = "/tmp/example-project"\ntitle = "Archive task"\n'
                            f'[[session]]\nagent = "codex"\nid = "{second}"\n'
                            'repo = "/tmp/example-project"\ntitle = "Other"\n')
            with patch.object(hotlist, "live_agents", return_value=[]), \
                 patch.object(hotlist_ipc, "request_revive", return_value=1) as send, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(hotlist.main(["--hotlist", str(path),
                                               "--id", f"codex:{first}"],
                                              action="request-revive"), 0)
            self.assertEqual([row["id"] for row in send.call_args.args[0]], [first])

    def test_socket_acknowledges_validated_requests_without_launching(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        session_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        row = {"agent": "claude", "id": session_id, "repo": "/tmp",
               "title": "Example"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.sock"
            received = []
            server = HotlistServer(received.extend, path)
            try:
                server.start()
            except OSError:
                with socket.socket(socket.AF_UNIX) as probe:
                    try:
                        probe.bind(str(Path(directory) / "probe.sock"))
                    except OSError as error:
                        if error.errno == errno.EPERM:
                            self.skipTest("sandbox forbids Unix domain socket bind")
                raise
            answer = []
            thread = threading.Thread(target=lambda: answer.append(
                hotlist_ipc.request_revive([row], path)))
            thread.start()
            deadline = time.monotonic() + 3
            while thread.is_alive() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            thread.join(timeout=1)
            app.processEvents()
            server.stop()
            self.assertEqual(answer, [1])
            self.assertEqual(received[0]["id"], session_id)
        with self.assertRaises(ValueError):
            validate_request({"action": "revive", "sessions": [
                {"agent": "claude", "id": session_id, "cwd": "/tmp",
                 "title": "x" * 201}]})


if __name__ == "__main__":
    unittest.main()
