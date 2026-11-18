"""Legacy tags stay recoverable while explicit organization is written."""

import json
import tempfile
import unittest
from pathlib import Path

from session_groups import SessionGroups
from tag_migration import apply_migration


class TagMigrationTest(unittest.TestCase):
    def test_choices_create_domain_group_and_note_with_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            tags_path = path / "tags.json"
            original = {"one": ["company", "swarm", "review"]}
            tags_path.write_text(json.dumps(original))
            groups = SessionGroups(path / "groups.json")
            plan = {"company": ("new_domain", "example-domain"),
                    "swarm": ("new_group", "Middleware"),
                    "review": ("note", "")}
            apply_migration(groups, original, plan, lambda key: "agent:codex:" + key,
                            tags_path)
            loaded = SessionGroups(path / "groups.json")
            key = "agent:codex:one"
            self.assertEqual(loaded.get_domain(loaded.domain_for([key]))["name"], "example-domain")
            self.assertEqual(loaded.get(loaded.group_for([key]))["name"], "Middleware")
            self.assertEqual(loaded.annotation_for([key]), "review")
            self.assertEqual(json.loads(tags_path.read_text()), {})
            self.assertEqual(json.loads((path / "tags.json.pre-phase4.bak").read_text()),
                             original)

    def test_conflicting_domain_choices_do_not_touch_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            tags_path = path / "tags.json"
            original = {"one": ["first", "second"]}
            tags_path.write_text(json.dumps(original))
            groups = SessionGroups(path / "groups.json")
            with self.assertRaisesRegex(ValueError, "different domains"):
                apply_migration(groups, original,
                                {"first": ("new_domain", "Life"),
                                 "second": ("new_domain", "Work")},
                                lambda key: "terminal:" + key, tags_path)
            self.assertEqual(json.loads(tags_path.read_text()), original)
            self.assertFalse((path / "tags.json.pre-phase4.bak").exists())


if __name__ == "__main__":
    unittest.main()
