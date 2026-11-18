"""Groups belong to durable sessions, independent of their current UI row."""

import tempfile
import unittest
from pathlib import Path

from session_groups import SessionGroups
from session_identity import agent_session_key, terminal_aliases, terminal_session_key


class SessionIdentityTest(unittest.TestCase):
    def test_agent_terminal_and_frozen_plan_keep_stable_keys(self):
        agent_id = "bc4b62ed-b9c0-5be2-9dd9-3386aaef9a34"
        agent_key = agent_session_key("claude", agent_id)
        terminal = {"key": "t3m", "agent": "claude", "session_id": agent_id,
                    "state": "live", "frozen_id": ""}
        self.assertEqual(terminal_session_key(terminal), agent_key)
        self.assertEqual(terminal_session_key({**terminal, "state": "dormant"}), agent_key)
        self.assertEqual(terminal_session_key({**terminal, "agent": "", "session_id": "",
                                               "frozen_id": "planned"}), "saved:planned")
        self.assertEqual(terminal_session_key({**terminal, "agent": "", "session_id": "",
                                               "frozen_id": ""}), "terminal:t3m")
        self.assertEqual(terminal_aliases([terminal]), {"terminal:t3m": agent_key})

    def test_existing_agent_membership_wins_when_terminal_aliases_migrate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            swarm = groups.create("Swarm")
            other = groups.create("Other")
            agent = "agent:claude:bc4b62ed-b9c0-5be2-9dd9-3386aaef9a34"
            alias = "terminal:t3m"
            standalone = "terminal:plain-shell"
            groups.assign([agent, standalone], swarm)
            groups.assign([alias], other)
            groups.set_annotation([agent], "r:maintainer o:/automation")
            groups.set_annotation([alias], "Terminal note")
            groups.member_order[other] = [alias]
            groups.save()
            self.assertGreater(groups.migrate_aliases({alias: agent}), 0)
            loaded = SessionGroups(path)
            self.assertEqual(loaded.group_for([agent]), swarm)
            self.assertNotIn(alias, loaded.members)
            self.assertEqual(loaded.group_for([standalone]), swarm)
            self.assertEqual(loaded.annotation_for([agent]), "r:maintainer o:/automation")
            self.assertEqual(loaded.annotation_for([alias]), "")
            self.assertEqual(loaded.member_order[other], [])
            self.assertEqual(loaded.migrate_aliases({alias: agent}), 0)

    def test_legacy_terminal_group_promotes_to_agent_when_agent_has_none(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            group = groups.create("Swarm")
            agent = "agent:codex:aa403547-b91b-504a-ac6f-0de875954184"
            alias = "terminal:middleware"
            groups.assign([alias], group)
            groups.set_annotation([alias], "r:maintainer o:/")
            groups.migrate_aliases({alias: agent})
            loaded = SessionGroups(path)
            self.assertEqual(loaded.group_for([agent]), group)
            self.assertEqual(loaded.annotation_for([agent]), "r:maintainer o:/")
            self.assertNotIn(alias, loaded.members)


if __name__ == "__main__":
    unittest.main()
