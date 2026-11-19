"""Manual session groups are independent of the terminal inventory."""

import json
import tempfile
import unittest
from pathlib import Path

from session_launch.session_groups import (SessionGroups, annotation_error, annotation_hierarchy_key,
                            annotation_markers, annotation_note_text)


class SessionGroupsTest(unittest.TestCase):
    def test_bulk_set_aside_preserves_organization_and_reuses_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            domain = groups.create_domain("example-domain")
            group = groups.create("repair")
            groups.set_group_domain(group, domain)
            for key in ("agent:claude:one", "agent:claude:two"):
                groups.add_membership([key], group)
            groups.set_retired_many([["agent:claude:one"], ["agent:claude:two"]], True)
            first_stamp = groups.retired["agent:claude:one"]
            groups.set_retired_many([["agent:claude:one"]], True)
            self.assertEqual(groups.retired["agent:claude:one"], first_stamp)
            loaded = SessionGroups(path)
            self.assertEqual(len(loaded.retired), 2)
            self.assertEqual(loaded.groups_for(["agent:claude:one"]), [group])
            self.assertEqual(loaded.domains_for(["agent:claude:two"]), [domain])
            loaded.set_retired_many([["agent:claude:one"], ["agent:claude:two"]], False)
            self.assertFalse(SessionGroups(path).retired)

    def test_annotation_keeps_prose_and_multiple_hats(self):
        line = ("Build the API and coordinate handoffs. r:maintainer r:coordinator "
                "o:/ t:/middleware custom:leave-this-alone")
        self.assertEqual(annotation_markers(line), {
            "r": ["maintainer", "coordinator"], "t": ["/middleware"], "o": ["/"]})
        self.assertEqual(annotation_note_text(line),
                         "Build the API and coordinate handoffs. custom:leave-this-alone")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            groups.set_annotation(["agent:codex:one", "terminal:one"], line)
            loaded = SessionGroups(path)
            self.assertEqual(loaded.annotation_for(["agent:codex:one"]), line)
            self.assertEqual(loaded.annotation_for(["terminal:one", "agent:codex:one"]), line)
            with self.assertRaises(ValueError):
                loaded.set_annotation(["agent:codex:one"], "two\nlines")

    def test_annotation_marker_validation_keeps_other_notes_freeform(self):
        self.assertEqual(annotation_error("Anything goes. x:unknown r:maintainer o:/ t:/auth/audit"), "")
        self.assertEqual(annotation_error("o:"), "o: needs a value.")
        self.assertEqual(annotation_error("t:auth"), "t: paths begin with / (for example, t:/auth).")
        with tempfile.TemporaryDirectory() as directory:
            groups = SessionGroups(Path(directory) / "groups.json")
            with self.assertRaisesRegex(ValueError, "o: needs a value"):
                groups.set_annotation(["agent:codex:one"], "o:")

    def test_domain_is_durable_and_follows_proven_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            group_id = groups.create("Swarm")
            groups.assign(["terminal:one"], group_id)
            domain = groups.create_domain("example-domain")
            groups.set_domain(["terminal:one"], domain)
            groups.migrate_aliases({"terminal:one": "agent:claude:uuid"})
            loaded = SessionGroups(path)
            self.assertEqual(loaded.group_for(["agent:claude:uuid"]), group_id)
            self.assertEqual(loaded.domain_for(["agent:claude:uuid"]), domain)
            self.assertNotIn("terminal:one", loaded.session_domains)
            with self.assertRaisesRegex(ValueError, "Unknown domain"):
                loaded.set_domain(["agent:claude:uuid"], "unknown")
            loaded.set_domain(["agent:claude:uuid"], None)
            self.assertIsNone(SessionGroups(path).domain_for(["agent:claude:uuid"]))

    def test_manual_link_moves_group_section_annotation_and_can_unlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            group = groups.create("Swarm")
            section = groups.create_section(group, "Overseers")
            alias = "terminal:window"
            canonical = "agent:claude:b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
            groups.assign_section([alias], group, section)
            groups.set_annotation([alias], "Middleware r:maintainer o:/middleware")
            moved = groups.link_terminal(alias, canonical)
            self.assertTrue(moved)
            loaded = SessionGroups(path)
            self.assertEqual(loaded.group_for([canonical]), group)
            self.assertEqual(loaded.section_for([canonical], group), section)
            self.assertIn("r:maintainer", loaded.annotation_for([canonical]))
            loaded.unlink_terminal(alias, canonical, moved)
            restored = SessionGroups(path)
            self.assertEqual(restored.group_for([alias]), group)
            self.assertEqual(restored.section_for([alias], group), section)
            self.assertIn("r:maintainer", restored.annotation_for([alias]))
            self.assertIsNone(restored.group_for([canonical]))

    def test_annotation_hierarchy_orders_scopes(self):
        lines = ["plain note", "t:/auth/audit", "o:/auth", "o:/", "o:/gui"]
        self.assertEqual(sorted(lines, key=annotation_hierarchy_key),
                         ["o:/", "o:/auth", "t:/auth/audit", "o:/gui", "plain note"])

    def test_ungrouped_session_overrides_stale_terminal_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            group_id = groups.create("Swarm")
            agent = "agent:claude:one"
            terminal = "terminal:window"
            groups.assign([agent, terminal], group_id)
            groups.assign([agent], None)  # Terminal link temporarily unavailable.
            loaded = SessionGroups(path)
            self.assertIsNone(loaded.group_for([agent, terminal]))
            self.assertEqual(loaded.members[terminal], group_id)
            loaded.assign([agent, terminal], None)
            self.assertNotIn(terminal, SessionGroups(path).members)
            other = "agent:codex:two"
            loaded.assign([agent, other], group_id)
            loaded.assign([agent, other], None)
            self.assertIsNone(SessionGroups(path).members[other])

    def test_existing_group_file_gains_roles_without_losing_members(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            path.write_text(json.dumps({
                "groups": [{"id": "old", "name": "Existing swarm", "collapsed": False}],
                "members": {"agent:codex:abc": "old"},
            }))
            groups = SessionGroups(path)
            self.assertEqual(groups.group_for(["agent:codex:abc"]), "old")
            self.assertIsNone(groups.domain_for(["agent:codex:abc"]))
            role = groups.create_section("old", "Overseers")
            groups.assign_section(["agent:codex:abc"], "old", role)
            loaded = SessionGroups(path)
            self.assertEqual(loaded.group_for(["agent:codex:abc"]), "old")
            self.assertEqual(loaded.section_for(["agent:codex:abc"], "old"), role)
            self.assertEqual(json.loads(path.read_text())["schema_version"], 2)
            self.assertTrue(path.with_name("groups.json.pre-v2.bak").exists())

    def test_v1_migration_preserves_domains_sections_and_backs_up_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            original = {"schema_version": 1,
                        "groups": [{"id": "g", "name": "Swarm", "collapsed": False}],
                        "members": {"agent:codex:a": "g"},
                        "sections": {"g": [{"id": "s", "name": "Overseers",
                                             "collapsed": False}]},
                        "member_sections": {"agent:codex:a": "s"},
                        "session_domains": {"agent:codex:a": "example-domain"}}
            path.write_text(json.dumps(original))
            groups = SessionGroups(path)
            self.assertEqual(json.loads(path.with_name("groups.json.pre-v2.bak").read_text()),
                             original)
            self.assertEqual(groups.section_for(["agent:codex:a"], "g"), "s")
            domain = groups.domain_for(["agent:codex:a"])
            self.assertEqual(groups.get_domain(domain)["name"], "example-domain")
            self.assertIsNone(groups.get("g")["domain"])
            self.assertEqual(json.loads(path.read_text())["schema_version"], 2)

    def test_multiple_memberships_and_all_metadata_follow_manual_link(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            domain = groups.create_domain("example-domain")
            first = groups.create("API")
            second = groups.create("Quality")
            groups.set_group_domain(first, domain)
            first_section = groups.create_section(first, "Overseers")
            second_section = groups.create_section(second, "Reviewers")
            alias, canonical = "terminal:one", "agent:codex:abc"
            groups.add_membership([alias], first, first_section)
            groups.add_membership([alias], second, second_section)
            groups.set_domain([alias], domain)
            groups.set_standing([alias], True)
            groups.set_lifecycle([alias], at_boot="always", on_stop="notify")
            groups.set_annotation([alias], "Own quality r:reviewer o:/")
            groups.set_retired([alias], True)
            moved = groups.link_terminal(alias, canonical)
            loaded = SessionGroups(path)
            self.assertEqual(loaded.groups_for([canonical]), [first, second])
            self.assertEqual(loaded.section_for([canonical], first), first_section)
            self.assertEqual(loaded.section_for([canonical], second), second_section)
            self.assertEqual(loaded.domains_for([canonical]), [domain])
            self.assertTrue(loaded.is_standing([canonical]))
            self.assertTrue(loaded.is_retired([canonical]))
            self.assertEqual(loaded.lifecycle_for([canonical])["at_boot"], "always")
            loaded.unlink_terminal(alias, canonical, moved)
            restored = SessionGroups(path)
            self.assertEqual(restored.groups_for([alias]), [first, second])
            self.assertTrue(restored.is_standing([alias]))
            self.assertTrue(restored.is_retired([alias]))
            self.assertEqual(restored.lifecycle_for([alias])["on_stop"], "notify")

    def test_changing_one_group_section_keeps_other_group_order(self):
        with tempfile.TemporaryDirectory() as directory:
            groups = SessionGroups(Path(directory) / "groups.json")
            first = groups.create("First")
            second = groups.create("Second")
            section = groups.create_section(first, "Overseers")
            key = "agent:codex:one"
            groups.add_membership([key], first)
            groups.add_membership([key], second)
            groups.member_order[first] = [key]
            groups.member_order[second] = [key]
            groups.save()
            groups.assign_section([key], first, section)
            self.assertEqual(groups.member_order[first], [])
            self.assertEqual(groups.member_order[second], [key])
            self.assertEqual(SessionGroups(groups.path).member_order[second], [key])

    def test_roles_and_member_order_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            group_id = groups.create("Swarm")
            senior = groups.create_section(group_id, "Top overseers")
            junior = groups.create_section(group_id, "Junior overseers")
            groups.assign(["agent:codex:a", "agent:codex:b", "agent:codex:c"], group_id)
            groups.assign_section(["agent:codex:a", "agent:codex:b"], group_id, senior)
            groups.assign_section(["agent:codex:c"], group_id, junior)
            groups.place_member(senior, "agent:codex:b", "agent:codex:a", True,
                                ["agent:codex:a", "agent:codex:b"])
            groups.place_section(junior, senior, True)
            loaded = SessionGroups(path)
            self.assertEqual([row["name"] for row in loaded.sections_for(group_id)],
                             ["Junior overseers", "Top overseers"])
            self.assertEqual(loaded.section_for(["agent:codex:b"], group_id), senior)
            self.assertEqual(loaded.member_order[senior], ["agent:codex:b", "agent:codex:a"])
            loaded.remove_section(senior)
            self.assertEqual(SessionGroups(path).section_for(["agent:codex:b"], group_id), None)
            self.assertEqual(SessionGroups(path).group_for(["agent:codex:b"]), group_id)

    def test_create_order_assign_collapse_and_remove_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            groups = SessionGroups(path)
            first = groups.create("Project urgent")
            second = groups.create("Infrastructure")
            groups.assign(["terminal:one", "agent:codex:abc"], first)
            groups.move(second, -1)
            groups.toggle(first)
            groups.rename(second, "Desk")
            loaded = SessionGroups(path)
            self.assertEqual([group["name"] for group in loaded.groups],
                             ["Desk", "Project urgent"])
            self.assertTrue(loaded.get(first)["collapsed"])
            self.assertEqual(loaded.group_for(["agent:codex:abc"]), first)
            with self.assertRaises(ValueError):
                loaded.rename(first, "desk")
            self.assertEqual(loaded.get(first)["name"], "Project urgent")
            loaded.remove(first)
            self.assertIsNone(SessionGroups(path).group_for(["terminal:one"]))


if __name__ == "__main__":
    unittest.main()
