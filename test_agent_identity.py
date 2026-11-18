"""Live IDs must be tied to the right process, never guessed from a stale file."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import agent_identity
import agent_catalog


class AgentIdentityTest(unittest.TestCase):
    def test_claude_pid_file_rejects_reused_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session_id = "2d7ac13b-e20b-5074-9fdb-88eaeee4d87d"
            row = {"pid": 42, "procStart": "12345", "cwd": "/repo",
                   "sessionId": session_id}
            (root / "42.json").write_text(json.dumps(row))
            process = {"pid": 42, "start_ticks": 12345, "cwd": "/repo"}
            self.assertEqual(agent_identity.claude_session_id(process, root), session_id)
            self.assertEqual(agent_identity.claude_session_id(
                {**process, "start_ticks": 12346}, root), "")

    def test_codex_rollout_requires_unique_time_and_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ticks = os.sysconf("SC_CLK_TCK")
            process = {"pid": 43, "start_ticks": 100 * ticks, "cwd": "/repo"}
            session_id = "04398305-7493-5b95-a7c1-a67ea699b840"

            def rollout(name, sid):
                row = {"type": "session_meta", "payload": {"id": sid, "cwd": "/repo",
                       "timestamp": "1970-01-01T00:18:25Z"}}
                (root / name).write_text(json.dumps(row) + "\n")

            rollout("rollout-one.jsonl", session_id)
            self.assertEqual(agent_identity.codex_session_id(process, root, 1000), session_id)
            rollout("rollout-two.jsonl", "5c6eecbe-a61a-537c-9576-cc38b4e2c381")
            agent_identity._codex_matches.clear()
            self.assertEqual(agent_identity.codex_session_id(process, root, 1000), "")

    def test_resume_preview_and_live_link(self):
        row = {"id": "codex:04398305-7493-5b95-a7c1-a67ea699b840",
               "agent": "codex", "cwd": "/tmp/a b"}
        self.assertEqual(agent_catalog.proposed_shell_command(row),
                         "cd -- '/tmp/a b' && codex resume 04398305-7493-5b95-a7c1-a67ea699b840")
        terminal = {"state": "live", "agent": "codex",
                    "session_id": "04398305-7493-5b95-a7c1-a67ea699b840"}
        self.assertIs(agent_catalog.linked_terminal(row, [terminal]), terminal)


if __name__ == "__main__":
    unittest.main()
