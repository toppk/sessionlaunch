"""Claude peer names are live addresses, linked by UUID and PID start tick."""

import json
import tempfile
import unittest
from pathlib import Path

import claude_peers


class ClaudePeersTest(unittest.TestCase):
    def test_reads_current_peer_and_rejects_stale_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sessions = root / "sessions"
            proc = root / "proc"
            sessions.mkdir()
            (proc / "42").mkdir(parents=True)
            fields = ["S", *(["0"] * 18), "12345"]
            (proc / "42" / "stat").write_text("42 (claude) " + " ".join(fields))
            session_id = "1e8a1429-57e3-538e-8ac6-3989c0119137"
            row = {"pid": 42, "procStart": "12345", "sessionId": session_id,
                   "name": "example-mail-9b", "status": "idle", "kind": "interactive",
                   "cwd": "/workspace/example-mail", "startedAt": 1794849017078}
            (sessions / "42.json").write_text(json.dumps(row))
            peers = claude_peers.read_peers(sessions, proc)
            self.assertEqual(claude_peers.peer_for(session_id, peers)["name"],
                             "example-mail-9b")
            self.assertEqual(peers[session_id]["cwd"], "/workspace/example-mail")
            self.assertTrue(claude_peers.started_at(peers[session_id]).startswith("2026-"))
            row["procStart"] = "12346"
            (sessions / "42.json").write_text(json.dumps(row))
            self.assertEqual(claude_peers.read_peers(sessions, proc), {})


if __name__ == "__main__":
    unittest.main()
