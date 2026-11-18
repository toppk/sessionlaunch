"""A link picker only offers exact provider and directory matches."""

import unittest
from unittest.mock import patch

import agent_link
import agent_catalog


class AgentLinkTest(unittest.TestCase):
    def test_unlinked_agent_in_same_folder_blocks_revival(self):
        row = {"id": "codex:b999622d-d0d5-587b-ab0f-af3d7cab1b4e",
               "agent": "codex", "cwd": "/tmp"}
        unknown = {"state": "live", "agent": "codex", "cwd": "/tmp",
                   "agent_cwd": "/tmp", "session_id": "", "pty": "/dev/pts/5"}
        self.assertIs(agent_catalog.unlinked_running_terminal(row, [unknown]), unknown)
        self.assertIsNone(agent_catalog.unlinked_running_terminal(
            row, [{**unknown, "session_id": "c721c33f-f89f-533e-83b2-65049fc05b99"}]))
        self.assertIsNone(agent_catalog.unlinked_running_terminal(
            row, [{**unknown, "agent_cwd": "/var/tmp"}]))

    def test_candidates_are_exact_matches_newest_first_with_messages(self):
        older = {"id": "b999622d-d0d5-587b-ab0f-af3d7cab1b4e", "agent": "claude",
                 "cwd": "/tmp", "started_at": "2026-10-10T00:00:00Z"}
        newer = {"id": "c721c33f-f89f-533e-83b2-65049fc05b99", "agent": "claude",
                 "cwd": "/tmp", "started_at": "2026-11-10T00:00:00Z"}
        other = {"id": "84a89d8e-85fd-512e-b206-3e476093d75f", "agent": "claude",
                 "cwd": "/var/tmp", "started_at": "2026-11-11T00:00:00Z"}

        def query(*args):
            if args[:2] == ("session", "list"):
                return ({"sessions": [newer]} if "--cursor" in args else
                        {"sessions": [older, other], "next_cursor": "second"})
            if args[:2] == ("session", "get"):
                return {"session": {"ended_at": "2026-11-12T00:00:00Z"}}
            return {"messages": [{"content": "Last message for " + args[2]}]}

        with patch("agent_link._query", side_effect=query) as queried:
            rows = agent_link.candidates("claude", "/tmp")
        self.assertEqual([row["id"] for row in rows], [newer["id"], older["id"]])
        self.assertTrue(all(row["last_message"].startswith("Last message") for row in rows))
        self.assertEqual(sum(call.args[:2] == ("session", "messages")
                             for call in queried.call_args_list), 2)
        self.assertEqual(sum(call.args[:2] == ("session", "list")
                             for call in queried.call_args_list), 2)


if __name__ == "__main__":
    unittest.main()
