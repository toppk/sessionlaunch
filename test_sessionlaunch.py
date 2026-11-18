"""Status names describe the current foreground process or a saved record."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ["SESSIONLAUNCH_DISABLE_SERVICE"] = "1"

from PySide6.QtCore import QItemSelectionModel, QMimeData, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QMessageBox, QPushButton,
                               QStyleOptionViewItem, QVBoxLayout, QWidget)

import manual_links
from session_launch.session_groups import SessionGroups
from sessionlaunch import (AnnotationDialog, LinkSessionDialog, card_presence, display_status,
                           elevation_reason, format_last_active,
                           record_facets, tag_label, terminal_directory, ResizeSurface,
                           SessionLaunch, SessionListModel)


class SessionLaunchTest(unittest.TestCase):
    def test_gui_reloads_external_groups_when_service_snapshot_is_unchanged(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"SESSIONLAUNCH_DISABLE_SERVICE": ""}), \
             patch("sessionlaunch.SERVICE_SOCKET", Path(directory) / "service.socket"), \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("sessionlaunch.sessionlaunch_service_client.request", side_effect=[
                 {"revision": 7, "data": data}, {"unchanged": True}]), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            Path(directory, "service.socket").touch()
            window = SessionLaunch([])
            try:
                external = SessionGroups(Path(directory) / "groups.json")
                domain = external.create_domain("work")
                group = external.create("saved-sessions")
                external.set_group_domain(group, domain)
                window.refresh_inventory()
                self.assertEqual(window.groups.get(group)["name"], "saved-sessions")
                self.assertTrue(any(window.navigation.ref_for(item) == ("group", group)
                                    for item in window.navigation.findItems(
                                        "*", Qt.MatchWildcard | Qt.MatchRecursive)))
            finally:
                window.close()

    def test_gui_reads_inventory_from_service_when_available(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        data = {"session": [], "terminal": [], "saved": [],
                "scan_status": "ok", "changed": True}
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"SESSIONLAUNCH_DISABLE_SERVICE": ""}), \
             patch("sessionlaunch.SERVICE_SOCKET", Path(directory) / "service.socket"), \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("sessionlaunch.sessionlaunch_service_client.request",
                   return_value={"ok": True, "revision": 7, "data": data,
                                 "service_pid": 42, "service_version": "0.2.2",
                                 "protocol_version": 1}) as service, \
             patch("inventory.update") as direct, \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            Path(directory, "service.socket").touch()
            window = SessionLaunch([])
            try:
                self.assertTrue(window.service_available)
                self.assertEqual(window.service_revision, 7)
                self.assertEqual(window.service_peer, (42, "0.2.2", 1))
                self.assertEqual(window.live_data, data)
                service.assert_called_once_with("snapshot", since=None, client="gui")
                direct.assert_not_called()
            finally:
                window.close()

    def test_titlebar_menu_contains_theme_and_color_key(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                self.assertIs(window.header_layout.itemAt(0).widget(), window.menu_button)
                self.assertIs(window.header_layout.itemAt(1).widget(), window.brand)
                window.theme_actions["dark"].trigger()
                self.assertEqual(window.theme, "dark")
                self.assertTrue(window.theme_actions["dark"].isChecked())
                self.assertFalse(window.theme_actions["light"].isChecked())
                color_key = next(action for action in window.menu.actions()
                                 if action.text() == "Color key…")
                with patch.object(QDialog, "exec", return_value=0) as show:
                    color_key.trigger()
                    show.assert_called_once()
            finally:
                window.close()

    def test_phase5b_scoped_search_presence_split_and_navigation_labels(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])

        def terminal(key, state):
            return {"key": key, "state": state, "pty": "/dev/pts/5", "agent": "",
                    "session_id": "", "terminal": "xterm", "terminal_pid": 40,
                    "host_application": "bash", "comm": "bash", "pid": 42, "pgid": 42,
                    "sid": 41, "argv": ["bash"], "cwd": "/tmp", "pwd_env": "/tmp",
                    "window_id": "", "uid": os.getuid(), "euid": os.geteuid(),
                    "processes": []}

        data = {"session": [], "terminal": [terminal("live-a", "live"),
                                           terminal("closed", "closed"),
                                           terminal("live-outside", "live")],
                "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                window.show()
                app.processEvents()
                domain = window.groups.create_domain("example-domain")
                group = window.groups.create("example-domain::reporting")
                window.groups.set_group_domain(group, domain)
                window.groups.assign(["terminal:live-a", "terminal:closed"], group)
                window.groups.set_annotation(["terminal:live-a"], "r:coordinator o:/")
                window.groups.set_annotation(["terminal:closed"], "r:coodinator o:/review")
                window.refresh_navigation()
                node = next(item for item in window.navigation.findItems(
                    "*", Qt.MatchWildcard | Qt.MatchRecursive)
                    if window.navigation.ref_for(item) == ("group", group))
                self.assertEqual(node.text(0), "reporting")
                self.assertIn("example-domain::reporting", node.toolTip(0))
                child = next(item for item in window.navigation.findItems(
                    "*", Qt.MatchWildcard | Qt.MatchRecursive)
                    if window.navigation.ref_for(item) == ("domain_ungrouped", domain))
                self.assertEqual(child.text(0), "Not in a group")

                window.change_view("plain")
                window.select_navigation(("group", group))
                self.assertEqual(window.search.placeholderText(), "Search in reporting")
                window.search.setText("bash")
                self.assertEqual(window._status_counts, {"running": 1, "not_running": 1})
                self.assertIn("1 more outside reporting", window.outside_search_button.text())
                window.outside_search_button.click()
                self.assertEqual(window.navigation_ref(), ("all", ""))
                self.assertEqual(window.search.placeholderText(), "Search in All sessions")
                self.assertEqual(window._status_counts, {"running": 2, "not_running": 1})
                self.assertFalse(window.outside_search_button.isVisible())

                window.set_split_sessions(True)
                refs = [row[SessionListModel.RefRole] for row in window.session_model.rows]
                self.assertEqual(refs[0], ("split", "running"))
                self.assertEqual(refs[3], ("split", "not_running"))
                self.assertEqual(window.session_model.index_for_ref(
                    ("terminal", "live-a")).data(SessionListModel.RoleHintsRole), {})
                self.assertEqual(window.session_model.index_for_ref(
                    ("terminal", "closed")).data(SessionListModel.RoleHintsRole),
                    {"coodinator": "coordinator"})
                self.assertEqual(window.count_label.text(), "PLAIN TERMINALS  ·  3")
                window.set_presence_view("not_running")
                self.assertFalse(window.split_button.isEnabled())
                self.assertEqual(window.state_filter.currentData(), "not_running")
                self.assertEqual(window.tags_button.text(), "Filter · 1 ▾")
                self.assertEqual([row[SessionListModel.RefRole]
                                  for row in window.session_model.rows],
                                 [("terminal", "closed")])
                self.assertEqual(window.presence_button_by_value["running"].text(), "Running 2")
                self.assertEqual(window.presence_button_by_value["all"].text(), "All 3")
                window.state_filter.setCurrentIndex(window.state_filter.findData("running"))
                self.assertEqual(window.presence_view, "running")
                self.assertTrue(window.presence_button_by_value["running"].isChecked())
                window.set_presence_view("all")
                self.assertTrue(window.split_button.isEnabled())
                self.assertEqual(window.state_filter.currentData(), "all")
                self.assertEqual(window.tags_button.text(), "Filter ▾")
                window.search.clear()
                window.select_navigation(("group", group))
                mime = QMimeData()
                mime.setData(window.session_list.MIME_TYPE,
                             json.dumps([["terminal", "live-a"]]).encode())
                self.assertTrue(window.session_list.valid_target(
                    mime, ("terminal", "live-a")))
                self.assertFalse(window.session_list.valid_target(
                    mime, ("terminal", "closed")))
                window.set_presence_view("running")
                window.clear_tag_filters()
                self.assertEqual(window.presence_view, "all")
                self.assertEqual(window.state_filter.currentData(), "all")
                self.assertEqual(window.tags_button.text(), "Filter ▾")
                saved = json.loads((Path(directory) / "appearance.json").read_text())
                self.assertEqual(saved["presence_view"], "all")
                self.assertTrue(saved["split_sessions"])
                self.assertTrue(window.group_counts.wordWrap())
            finally:
                window.close()
            window = SessionLaunch([])
            try:
                self.assertEqual(window.presence_view, "all")
                self.assertTrue(window.split_sessions)
                self.assertEqual(window.navigation_ref(), ("group", group))
            finally:
                window.close()

    def test_phase4_standing_uuid_remains_visible_without_catalog_entry(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        session_id = "aa403547-b91b-504a-ac6f-0de875954184"
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch.object(SessionLaunch, "query_agent_detail"), \
             patch.object(SessionLaunch, "query_agent_messages"):
            window = SessionLaunch([])
            try:
                window.groups.set_standing([f"agent:codex:{session_id}"], True)
                window.select_navigation(("standing", ""))
                window.refresh_list()
                index = window.session_model.index_for_ref(("agent", f"codex:{session_id}"))
                self.assertTrue(index.isValid())
                window.session_list.setCurrentIndex(index)
                self.assertIn(session_id, window.session_values["Session ID"])
                self.assertFalse(window.launch_button.isEnabled())
            finally:
                window.close()

    def test_phase4_stop_notice_does_not_restart_agent(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        key = "agent:codex:aa403547-b91b-504a-ac6f-0de875954184"
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                window.groups.set_lifecycle([key], on_stop="notify")
                window.observed_agent_keys = {key}
                with patch.object(window, "launch_agent") as launch:
                    window.refresh_inventory(force=True)
                    launch.assert_not_called()
                self.assertIn(key, window.stop_alerts)
                self.assertIn("Agent stopped", window.status.text())
            finally:
                window.close()

    def test_phase4_background_peer_exit_notifies_without_restart(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        session_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        key = f"agent:claude:{session_id}"
        peer = {session_id: {"name": "voice", "pid": 12345, "session_id": session_id}}
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("inventory.update", return_value=data), \
             patch("claude_peers.read_peers", return_value=peer), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                window.groups.set_lifecycle([key], on_stop="notify")
                with patch("claude_peers.read_peers", return_value={}), \
                     patch("sessionlaunch.os.kill", side_effect=ProcessLookupError), \
                     patch.object(window, "launch_agent") as launch:
                    window.refresh_claude_peers()
                    launch.assert_not_called()
                self.assertIn(key, window.stop_alerts)
            finally:
                window.close()

    def test_phase4_domain_checklist_deduplicates_and_respects_lifecycle(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        ids = ["b999622d-d0d5-587b-ab0f-af3d7cab1b4e",
               "9ccdcca5-b51f-5c03-b858-853c5901b56e"]
        rows = [{"id": f"codex:{session_id}", "agent": "codex", "cwd": "/tmp",
                 "display_name": f"Agent {index}", "project": "test"}
                for index, session_id in enumerate(ids)]
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch.object(SessionLaunch, "query_agent_detail"), \
             patch.object(SessionLaunch, "query_agent_messages"):
            window = SessionLaunch([])
            try:
                window.agent_rows = rows
                window.agent_by_id = {row["id"]: row for row in rows}
                domain = window.groups.create_domain("example-domain")
                first = window.groups.create("API")
                second = window.groups.create("Review")
                window.groups.set_group_domain(first, domain)
                window.groups.set_group_domain(second, domain)
                keys = [f"agent:codex:{session_id}" for session_id in ids]
                window.groups.add_membership([keys[0]], first)
                window.groups.add_membership([keys[0]], second)
                window.groups.add_membership([keys[1]], second)
                window.groups.set_standing([keys[0]], True)
                window.groups.set_lifecycle([keys[0]], at_boot="always")
                window.groups.set_lifecycle([keys[1]], at_boot="never")
                window.show_group_pane(("domain", domain))
                self.assertEqual(len(window.group_members), 2)
                self.assertEqual(list(window.group_checkboxes), [keys[0]])
                self.assertTrue(window.group_checkboxes[keys[0]].isChecked())
                self.assertIn("1 set to never", window.never_toggle.text())
                self.assertFalse(window.never_widget.isVisible())
                window.show_group_pane(("domain", domain))
                self.assertTrue(window.group_checkboxes[keys[0]].isChecked())
            finally:
                window.close()

    def test_link_picker_requires_an_explicit_selection(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        candidate = {"id": "b999622d-d0d5-587b-ab0f-af3d7cab1b4e", "agent": "claude",
                     "cwd": "/tmp", "started_at": "2026-11-12T00:00:00Z",
                     "ended_at": "2026-11-16T00:00:00Z", "last_message": "Confirm the API"}
        dialog = LinkSessionDialog(None, "claude", "/tmp")
        try:
            dialog.populate_candidates([candidate])
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertFalse(dialog.choose.isEnabled())
            dialog.table.selectRow(0)
            self.assertTrue(dialog.choose.isEnabled())
            self.assertEqual(dialog.selected_candidate()["id"], candidate["id"])
        finally:
            dialog.close()

    def test_link_picker_marks_attached_uuid_in_use_and_starts_async_query(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        session_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        parent.live_data = {"terminal": [{"key": "other", "state": "live", "agent": "codex",
                                           "session_id": session_id, "pty": "/dev/pts/5"}]}
        candidate = {"id": f"codex:{session_id}", "agent": "codex", "cwd": "/tmp"}
        dialog = LinkSessionDialog(parent, "codex", "/tmp")
        try:
            with patch("sessionlaunch.QProcess.start") as start:
                dialog.show()
                app.processEvents()
                start.assert_called_once()
                self.assertEqual(start.call_args.args[0], os.sys.executable)
            dialog.populate_candidates([candidate])
            self.assertIn("in use · /dev/pts/5", dialog.table.item(0, 0).text())
            dialog.table.selectRow(0)
            self.assertIsNone(dialog.selected_candidate())
            self.assertFalse(dialog.choose.isEnabled())
        finally:
            dialog.close()
            parent.close()

    def test_card_layout_prioritizes_scope_and_scales_with_zoom(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        agent_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        row = {"id": agent_id, "agent": "claude", "cwd": "/tmp",
               "display_name": "Archive task", "project": "example-project"}
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch.object(SessionLaunch, "query_agent_detail"), \
             patch.object(SessionLaunch, "query_agent_messages"):
            window = SessionLaunch([])
            try:
                window.agent_rows = [row]
                window.agent_by_id = {agent_id: row}
                window.groups.set_annotation([f"agent:claude:{agent_id}"],
                                             "Own daily archive. r:maintainer o:/dailyarchive")
                window.change_view("sessions")
                window.refresh_list()
                index = window.session_model.index_for_ref(("agent", agent_id))
                self.assertEqual(index.data(SessionListModel.RoleHintsRole), {})
                delegate = window.session_list.itemDelegate()

                def geometry(pixel_size, zoom):
                    window.zoom = zoom
                    font = QFont(window.session_list.font())
                    font.setPixelSize(pixel_size)
                    window.session_list.setFont(font)
                    window.refresh_list()
                    current = window.session_model.index_for_ref(("agent", agent_id))
                    option = QStyleOptionViewItem()
                    option.font = font
                    option.fontMetrics = QFontMetrics(font)
                    option.rect = QRect(0, 0, 355,
                                        current.data(Qt.SizeHintRole).height())
                    return delegate.card_geometry(option, current), option

                normal, option = geometry(13, 0)
                mono = QFont(option.font)
                mono.setFamily("monospace")
                mono.setBold(True)
                self.assertGreaterEqual(normal["headline"].width(),
                                        QFontMetrics(mono).horizontalAdvance("o:/dailyarchive"))
                option.rect = QRect(0, 0, 310, option.rect.height())
                compact = delegate.card_geometry(option, index)
                self.assertEqual(compact["role_text"], "maintainer")
                self.assertIsNotNone(compact["role"])
                self.assertLess(compact["role"].width(),
                                compact["small_metrics"].horizontalAdvance("maintainer") + 16)
                enlarged, option = geometry(21, 8)
                self.assertLess(enlarged["headline"].right(), enlarged["status"].left())
                self.assertLess(enlarged["note"].bottom(), enlarged["footer"][0][2].top())
                self.assertLess(enlarged["footer"][0][2].bottom(), enlarged["card"].bottom())
            finally:
                window.close()
        self.assertIsNotNone(app)

    def test_card_presence_distinguishes_unlinked_agent_terminal(self):
        self.assertEqual(card_presence(("terminal", "pty"), "running", "claude", ""),
                         "terminal only")
        self.assertEqual(card_presence(("terminal", "pty"), "running", "claude", "uuid"),
                         "running")
        self.assertEqual(card_presence(("agent", "uuid"), "saved", "codex", "uuid"),
                         "resumable")
        self.assertEqual(card_presence(("saved", "frozen"), "saved", "", ""),
                         "frozen")
        self.assertEqual(card_presence(("terminal", "history"), "saved", "", ""),
                         "closed terminal")

    def test_annotation_role_typo_hint_does_not_block_save(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        with tempfile.TemporaryDirectory() as directory:
            from session_groups import SessionGroups
            parent.groups = SessionGroups(Path(directory) / "groups.json")
            parent.groups.set_annotation(["agent:claude:other"], "r:coordinator o:/")
            dialog = AnnotationDialog(parent, "")
            try:
                dialog.line_edit.setText("r:coodinator o:/auth")
                self.assertIn("did you mean r:coordinator", dialog.validation.text())
                self.assertTrue(dialog.save_button.isEnabled())
            finally:
                dialog.close()
                parent.close()
        self.assertIsNotNone(app)

    def test_model_refresh_keeps_selection_and_scroll_anchor(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        terminals = [
            {"key": f"terminal-{number}", "state": "live", "pty": f"/dev/pts/{number}",
             "agent": "", "session_id": "", "terminal": "xterm", "terminal_pid": number + 100,
             "host_application": "bash", "comm": "bash", "pid": number + 200,
             "pgid": number + 200, "sid": number + 200, "argv": ["bash"],
             "cwd": "/tmp", "pwd_env": "/tmp", "window_id": "",
             "uid": os.getuid(), "euid": os.geteuid(), "processes": []}
            for number in range(40)
        ]
        data = {"session": [], "terminal": terminals, "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                window.change_view("plain")
                window.show()
                app.processEvents()
                chosen = ("terminal", "terminal-24")
                window.session_list.setCurrentIndex(window.session_model.index_for_ref(chosen))
                companion = ("terminal", "terminal-25")
                window.session_list.selectionModel().select(
                    window.session_model.index_for_ref(companion), QItemSelectionModel.Select)
                window.session_list.scrollTo(window.session_model.index_for_ref(chosen),
                                             QAbstractItemView.PositionAtTop)
                app.processEvents()
                top = window.session_list.indexAt(QPoint(8, 2)).data(SessionListModel.RefRole)
                self.assertIsNotNone(top)
                window.refresh_list()
                app.processEvents()
                self.assertEqual(window.selected_ref(), chosen)
                self.assertIn(companion, [index.data(SessionListModel.RefRole)
                                         for index in window.session_list.selectedIndexes()])
                self.assertEqual(window.session_list.indexAt(QPoint(8, 2)).data(
                    SessionListModel.RefRole), top)
            finally:
                window.close()

    def test_shift_and_ctrl_click_keep_bulk_selection(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        terminals = [
            {"key": f"window-{number}", "state": "live", "pty": f"/dev/pts/{number}",
             "agent": "", "session_id": "", "terminal": "xterm", "terminal_pid": number + 100,
             "host_application": "bash", "comm": "bash", "pid": number + 200,
             "pgid": number + 200, "sid": number + 200, "argv": ["bash"],
             "cwd": "/tmp", "pwd_env": "/tmp", "window_id": "",
             "uid": os.getuid(), "euid": os.geteuid(), "processes": []}
            for number in range(4)
        ]
        data = {"session": [], "terminal": terminals, "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                window.change_view("plain")
                window.show()
                app.processEvents()
                view = window.session_list

                def click(row, modifier=Qt.NoModifier):
                    point = view.visualRect(window.session_model.index(row, 0)).center()
                    QTest.mouseClick(view.viewport(), Qt.LeftButton, modifier, point)
                    app.processEvents()

                click(0)
                window.refresh_list()
                click(2, Qt.ShiftModifier)
                self.assertEqual(len(view.selectedIndexes()), 3)
                click(3, Qt.ControlModifier)
                self.assertEqual(len(view.selectedIndexes()), 4)
                point = view.visualRect(window.session_model.index(2, 0)).center()
                QTest.mousePress(view.viewport(), Qt.RightButton, Qt.NoModifier, point)
                app.processEvents()
                self.assertEqual(len(view.selectedIndexes()), 4)
                window.refresh_list()
                self.assertEqual(len(view.selectedIndexes()), 4)
                domain = window.groups.create_domain("example-domain")
                refs = [index.data(SessionListModel.RefRole) for index in view.selectedIndexes()]
                window.assign_domain(refs, domain)
                self.assertEqual({window.groups.domain_for([f"terminal:window-{number}"])
                                  for number in range(4)}, {domain})
            finally:
                window.close()

    def test_claude_peer_name_is_searchable_and_shows_stable_id(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        agent_id = "1e8a1429-57e3-538e-8ac6-3989c0119137"
        peer = {"session_id": agent_id, "name": "example-mail-9b", "pid": 42,
                "cwd": "/tmp", "kind": "interactive", "status": "idle",
                "started_at": 1794849017078, "updated_at": 1794849017078}
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("inventory.update", return_value=data), \
             patch("claude_peers.read_peers", return_value={agent_id: peer}), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch.object(SessionLaunch, "query_agent_detail"), \
             patch.object(SessionLaunch, "query_agent_messages"):
            window = SessionLaunch([])
            try:
                window.change_view("sessions")
                row = {"id": agent_id, "agent": "claude", "cwd": "/tmp",
                       "display_name": "Mail work", "project": "example-mail"}
                window.agent_rows = [row]
                window.agent_by_id = {agent_id: row}
                window.groups.set_annotation(
                    window.group_keys_for_ref(("agent", agent_id)),
                    "Coordinate middleware r:maintainer o:/middleware t:/auth/audit")
                window.agent_kind.setCurrentIndex(2)  # Claude peers
                window.search.setText("example-mail-9b")
                item = next(window.session_model.index(i, 0)
                            for i in range(window.session_model.rowCount())
                            if window.session_model.index(i, 0).data(SessionListModel.RefRole) ==
                            ("agent", agent_id))
                self.assertIn("peer:example-mail-9b", item.data(SessionListModel.TagsRole))
                self.assertEqual(item.data(SessionListModel.PeerNameRole), "example-mail-9b")
                self.assertEqual(item.data(SessionListModel.ProviderRole), "claude")
                self.assertEqual(item.data(SessionListModel.SessionUuidRole), agent_id)
                self.assertEqual(item.data(SessionListModel.CardPresenceRole), "running")
                self.assertIsNone(item.data(SessionListModel.DomainRole))
                window.session_list.setCurrentIndex(item)
                app.processEvents()
                self.assertEqual(window.session_values["Session ID"], agent_id)
                self.assertIn("example-mail-9b", window.session_values["Claude peer"])
                self.assertIn("2026-", window.session_values["Peer process started"])
                self.assertEqual(window.subject.text(), "o:/middleware")
                self.assertEqual(window.annotation_block.note.text(), "Coordinate middleware")
                self.assertEqual(window.annotation_block.markers.count(), 4)  # three pills + stretch
                self.assertIn("temporary name", window.attachment_grid.itemAtPosition(0, 0).widget().text())
            finally:
                window.close()

    def test_external_inventory_update_marks_existing_agent_active(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        agent_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        agent = {"id": agent_id, "agent": "claude", "cwd": "/tmp",
                 "display_name": "Revived work", "project": "demo"}
        terminal = {"key": "external-terminal", "state": "live", "pty": "/dev/pts/5",
                    "agent": "claude", "session_id": agent_id, "terminal": "terminal",
                    "terminal_pid": 40, "host_application": "claude", "comm": "claude",
                    "pid": 42, "pgid": 42, "sid": 41, "argv": ["claude"],
                    "cwd": "/tmp", "pwd_env": "/tmp", "window_id": "",
                    "uid": os.getuid(), "euid": os.geteuid(), "processes": []}
        initial = {"session": [], "terminal": [], "saved": [], "scan_status": "ok",
                   "changed": False}
        external = {**initial, "terminal": [terminal]}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", side_effect=[initial, external]), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch.object(SessionLaunch, "query_agent_detail"), \
             patch.object(SessionLaunch, "query_agent_messages"):
            window = SessionLaunch([])
            try:
                window.change_view("sessions")
                app.processEvents()
                window.agent_rows = [agent]
                window.agent_by_id = {agent_id: agent}
                window.set_presence_view("running")
                self.assertFalse(any(window.session_model.index(i, 0).data(SessionListModel.RefRole) ==
                                     ("agent", agent_id)
                                     for i in range(window.session_model.rowCount())))
                window.refresh_inventory()
                active = next(window.session_model.index(i, 0)
                              for i in range(window.session_model.rowCount())
                              if window.session_model.index(i, 0).data(SessionListModel.RefRole) ==
                              ("agent", agent_id))
                self.assertEqual(active.data(SessionListModel.PresenceRole), "running")
            finally:
                window.close()

    def test_group_pane_uses_persisted_members_and_one_revival_confirmation(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        agent_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        agent = {"id": agent_id, "agent": "claude", "cwd": "/tmp",
                 "display_name": "Middleware", "project": "example-project"}
        terminal = {"key": "shell", "state": "live", "pty": "/dev/pts/5",
                    "agent": "", "session_id": "", "terminal": "terminal",
                    "terminal_pid": 40, "host_application": "bash", "comm": "bash",
                    "pid": 42, "pgid": 42, "sid": 41, "argv": ["bash"],
                    "cwd": "/tmp", "pwd_env": "/tmp", "window_id": "0x1",
                    "uid": os.getuid(), "euid": os.geteuid(), "processes": []}
        old_terminal = {**terminal, "key": "old-shell", "state": "dormant",
                        "window_id": "", "closed_at": "2026-11-16T00:00:00Z"}
        data = {"session": [], "terminal": [terminal, old_terminal],
                "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch.object(SessionLaunch, "query_agent_detail"), \
             patch.object(SessionLaunch, "query_agent_messages"):
            window = SessionLaunch([])
            try:
                window.agent_rows = [agent]
                window.agent_by_id = {agent_id: agent}
                group = window.groups.create("Swarm")
                section = window.groups.create_section(group, "Overseers")
                agent_key = window.group_keys_for_ref(("agent", agent_id))[0]
                window.groups.assign_section([agent_key], group, section)
                window.groups.assign(["terminal:shell", "terminal:old-shell"], group)
                window.groups.set_annotation([agent_key], "Own middleware r:maintainer o:/")
                window.refresh_navigation()
                window.select_navigation(("group", group))
                tooltip = window.navigation.currentItem().toolTip(0)
                self.assertIn("Double-click or ←/→", tooltip)
                self.assertNotIn("Click to expand", tooltip)
                app.processEvents()
                self.assertFalse(window.group_pane.isHidden())
                self.assertIn("1 plain shell", window.group_counts.text())
                self.assertEqual(len(window.group_members), 3)
                self.assertTrue(window.group_checkboxes[agent_key].isEnabled())
                self.assertFalse(window.group_checkboxes["terminal:old-shell"].isEnabled())
                self.assertEqual(window.reboot_layout.count(), 2)
                window.group_checkboxes[agent_key].setChecked(True)
                with patch("sessionlaunch.QMessageBox.question", return_value=QMessageBox.Yes) as ask, \
                     patch.object(window, "launch_agent") as launch:
                    window.revive_checked_group()
                self.assertEqual(ask.call_count, 1)
                launch.assert_called_once()
                self.assertTrue(launch.call_args.kwargs["confirmed"])
                window.select_navigation(("section", section))
                self.assertEqual(len(window.group_members), 1)
                window.claude_peers = {agent_id: {"session_id": agent_id,
                                                  "name": "middleware-peer", "pid": 55,
                                                  "cwd": "/tmp", "status": "idle",
                                                  "kind": "interactive"}}
                window.show_group_pane(("group", group))
                self.assertIn("1 agent running", window.group_counts.text())
                self.assertNotIn(agent_key, window.group_checkboxes)
                window.select_navigation(("group", group))
                window.change_view("plain")
                window.session_list.setCurrentIndex(
                    window.session_model.index_for_ref(("terminal", "shell")))
                self.assertEqual(window.session_values["Provider"], "None (plain terminal)")
            finally:
                window.close()

    def test_manual_link_and_unlink_move_terminal_organization(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        agent_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        terminal = {"key": "window", "state": "live", "pty": "/dev/pts/5",
                    "agent": "claude", "agent_pid": 50, "agent_start_ticks": 1234,
                    "agent_cwd": "/tmp",
                    "session_id": "", "session_id_source": "", "terminal": "terminal",
                    "terminal_pid": 40, "host_application": "claude", "comm": "claude",
                    "pid": 50, "pgid": 50, "sid": 41, "argv": ["claude"],
                    "cwd": "/tmp", "pwd_env": "/tmp", "window_id": "",
                    "uid": os.getuid(), "euid": os.geteuid(), "processes": []}
        data = {"session": [], "terminal": [terminal], "saved": [], "scan_status": "ok"}
        candidate = {"id": agent_id, "agent": "claude", "cwd": "/tmp"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("manual_links.FILE", Path(directory) / "manual_links.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch("sessionlaunch.LinkSessionDialog.exec", return_value=QDialog.Accepted), \
             patch("sessionlaunch.LinkSessionDialog.selected_candidate", return_value=candidate), \
             patch("sessionlaunch.QMessageBox.question", return_value=QMessageBox.Yes):
            window = SessionLaunch([])
            try:
                group = window.groups.create("Swarm")
                section = window.groups.create_section(group, "Overseers")
                window.groups.assign_section(["terminal:window"], group, section)
                window.groups.set_annotation(["terminal:window"], "Middleware o:/")
                window.refresh_navigation()
                window.select_navigation(("group", group))
                window.refresh_list()
                window.session_list.setCurrentIndex(
                    window.session_model.index_for_ref(("terminal", "window")))
                with patch.object(window, "refresh_inventory"):
                    window.link_action()
                    canonical = f"agent:claude:{agent_id}"
                    self.assertEqual(window.groups.group_for([canonical]), group)
                    self.assertEqual(window.groups.section_for([canonical], group), section)
                    self.assertEqual(terminal["session_id_source"], "manual")
                    self.assertIn("window", manual_links.load())
                    terminal["state"] = "dormant"
                    window.refresh_list()
                    window.session_list.setCurrentIndex(
                        window.session_model.index_for_ref(("agent", agent_id)))
                    window.link_action()
                self.assertEqual(window.groups.group_for(["terminal:window"]), group)
                self.assertEqual(window.groups.section_for(["terminal:window"], group), section)
                self.assertEqual(terminal["session_id"], "")
                self.assertEqual(manual_links.load(), {})
                terminal["state"] = "live"
                data["terminal"].append({**terminal, "key": "other", "pty": "/dev/pts/9",
                                         "session_id": agent_id})
                window.refresh_list()
                window.select_navigation(("group", group))
                window.session_list.setCurrentIndex(
                    window.session_model.index_for_ref(("terminal", "window")))
                with patch.object(window, "refresh_inventory"), \
                     patch("sessionlaunch.QMessageBox.warning") as warning:
                    window.link_action()
                self.assertIn("already in use", warning.call_args.args[2])
                self.assertEqual(manual_links.load(), {})
            finally:
                window.close()

    def test_group_checklist_blocks_unlinked_agent_and_codex_key_is_stable(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        agent_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        old = {"key": "old", "state": "dormant", "agent": "codex",
               "session_id": agent_id, "cwd": "/tmp", "pty": "/dev/pts/2", "comm": "codex",
               "terminal": "terminal"}
        unlinked = {"key": "current", "state": "live", "agent": "codex",
                    "session_id": "", "agent_cwd": "/tmp", "cwd": "/tmp",
                    "pty": "/dev/pts/5", "host_application": "codex", "comm": "codex",
                    "terminal": "terminal"}
        for terminal in (old, unlinked):
            terminal.update(terminal_pid=40, pid=50, pgid=50, sid=41,
                            argv=["codex"], pwd_env="/tmp", window_id="",
                            uid=os.getuid(), euid=os.geteuid(), processes=[])
        data = {"session": [], "terminal": [old, unlinked], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                key = f"agent:codex:{agent_id}"
                group = window.groups.create("Swarm")
                window.groups.assign([key, "terminal:current"], group)
                window.show_group_pane(("group", group))
                check = window.group_checkboxes[key]
                self.assertFalse(check.isEnabled())
                self.assertIn("running unlinked in pts/5", check.toolTip())
                self.assertIn("1 agent running", window.group_counts.text())
                self.assertTrue(window.group_members["terminal:current"]["unlinked_agent"])
                with patch("sessionlaunch.restore.launch") as launch:
                    window.launch_agent({"id": f"codex:{agent_id}", "agent": "codex",
                                         "cwd": "/tmp"}, confirmed=True)
                    launch.assert_not_called()
                data["terminal"] = [old]
                window.refresh_inventory(force=True)
                member = window.group_member_view(key)
                self.assertEqual(member["row"]["id"], agent_id)
                self.assertTrue(member["can_revive"])
                with patch("sessionlaunch.install_rcfile"), \
                     patch("sessionlaunch.restore.launch") as launch, \
                     patch.object(window, "act_window", return_value=True):
                    window.launch_agent(member["row"], confirmed=True)
                    self.assertIn(key, window.terminals)
                    window.launch_agent({"id": f"codex:{agent_id}", "agent": "codex",
                                         "cwd": "/tmp"}, confirmed=True)
                    launch.assert_called_once()
            finally:
                window.close()

    def test_phase5_navigation_membership_and_restart(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        agent_id = "4e5da2ba-0d5e-5c4d-a697-94d3a534ff4a"

        def terminal(key, host, agent="", session_id=""):
            return {"key": key, "state": "live", "pty": "/dev/pts/5",
                    "agent": agent, "session_id": session_id, "terminal": "terminal",
                    "terminal_pid": 40, "host_application": host, "comm": host,
                    "pid": 42, "pgid": 42, "sid": 41, "argv": [host],
                    "cwd": "/tmp", "pwd_env": "/tmp", "window_id": "",
                    "uid": os.getuid(), "euid": os.geteuid(), "processes": []}

        records = [terminal("agent-window", "codex", "codex", agent_id),
                   terminal("plain-window", "bash"),
                   terminal("domain-only-window", "bash")]
        data = {"session": [], "terminal": records, "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch.object(SessionLaunch, "query_agent_detail"), \
             patch.object(SessionLaunch, "query_agent_messages"):
            window = SessionLaunch([])
            try:
                group_a = window.groups.create("Work now")
                group_b = window.groups.create("Later")
                agent_ref = ("agent", f"codex:{agent_id}")
                window.assign_group(agent_ref, group_a)
                window.select_navigation(("group", group_a))
                refs = [row[SessionListModel.RefRole] for row in window.session_model.rows]
                self.assertEqual(refs, [agent_ref])  # Linked terminal is the same card.
                self.assertEqual(window.count_label.text(), "SESSIONS  ·  1")
                self.assertEqual(window.session_list.selectionMode(),
                                 QAbstractItemView.ExtendedSelection)
                item = window.navigation.currentItem()
                item.setExpanded(False)
                self.assertTrue(window.groups.get(group_a)["collapsed"])
                self.assertEqual(window.session_model.rowCount(), 1)
                window.change_view("plain")
                window.select_navigation(("ungrouped", ""))
                window.show()
                app.processEvents()
                self.assertTrue(window.session_model.index_for_ref(
                    ("terminal", "plain-window")).isValid())

                class DragEvent:
                    def __init__(self, target, ctrl=False):
                        self.target = target
                        self.ctrl = ctrl
                        self.mime = QMimeData()
                        self.mime.setData(window.session_list.MIME_TYPE,
                                          json.dumps([["terminal", "plain-window"]]).encode())
                        self.accepted = False

                    def source(self):
                        return window.session_list

                    def position(self):
                        item = next(item for item in window.navigation.findItems(
                            "*", Qt.MatchWildcard | Qt.MatchRecursive)
                            if window.navigation.ref_for(item) == ("group", self.target))
                        rect = window.navigation.visualItemRect(item)
                        return QPointF(rect.center())

                    def mimeData(self):
                        return self.mime

                    def modifiers(self):
                        return Qt.ControlModifier if self.ctrl else Qt.NoModifier

                    def setDropAction(self, action):
                        self.action = action

                    def accept(self):
                        self.accepted = True

                    def ignore(self):
                        self.accepted = False

                window.session_list.drag_source_group = None
                first = DragEvent(group_a)
                window.navigation.dropEvent(first)
                self.assertTrue(first.accepted)
                self.assertEqual(window.groups.groups_for(["terminal:plain-window"]), [group_a])
                window.select_navigation(("group", group_a))
                window.session_list.drag_source_group = group_a
                second = DragEvent(group_b, ctrl=True)
                window.navigation.dropEvent(second)
                self.assertTrue(second.accepted)
                self.assertEqual(set(window.groups.groups_for(["terminal:plain-window"])),
                                 {group_a, group_b})
                domain = window.groups.create_domain("example-domain")
                window.refresh_navigation()

                domain_ref = ("terminal", "domain-only-window")
                domain_menu = window.session_menu(domain_ref)
                move_domain = next(action.menu() for action in domain_menu.actions()
                                   if action.menu() and action.menu().title() == "Move to domain")
                next(action for action in move_domain.actions()
                     if action.text() == "example-domain").trigger()
                self.assertEqual(window.groups.domain_for(["terminal:domain-only-window"]),
                                 domain)
                self.assertEqual(window.groups.groups_for(["terminal:domain-only-window"]), [])
                domain_menu = window.session_menu(domain_ref)
                move_domain = next(action.menu() for action in domain_menu.actions()
                                   if action.menu() and action.menu().title() == "Move to domain")
                next(action for action in move_domain.actions()
                     if action.text() == "No direct domain").trigger()
                self.assertIsNone(window.groups.domain_for(["terminal:domain-only-window"]))
                window.select_navigation(("ungrouped", ""))
                self.assertTrue(window.session_model.index_for_ref(domain_ref).isValid())

                class DomainDragEvent(DragEvent):
                    def __init__(self):
                        super().__init__(group_a)
                        self.mime.setData(window.session_list.MIME_TYPE,
                                          json.dumps([["terminal", "domain-only-window"]]).encode())

                    def position(self):
                        item = next(item for item in window.navigation.findItems(
                            "*", Qt.MatchWildcard | Qt.MatchRecursive)
                            if window.navigation.ref_for(item) == ("domain", domain))
                        return QPointF(window.navigation.visualItemRect(item).center())

                domain_drag = DomainDragEvent()
                self.assertEqual(window.navigation.ref_for(
                    window.navigation.itemAt(domain_drag.position().toPoint())),
                    ("domain", domain))
                window.navigation.dropEvent(domain_drag)
                self.assertTrue(domain_drag.accepted)
                self.assertEqual(window.groups.domain_for(["terminal:domain-only-window"]),
                                 domain)
                self.assertEqual(window.groups.groups_for(["terminal:domain-only-window"]), [])
                window.select_navigation(("domain", domain))
                self.assertTrue(window.session_model.index_for_ref(domain_ref).isValid())

                class GroupDragEvent(DragEvent):
                    def __init__(self):
                        super().__init__(group_a)
                        self.mime = QMimeData()
                        self.mime.setData(window.session_list.GROUP_MIME,
                                          group_a.encode())

                    def source(self):
                        return window.navigation

                    def position(self):
                        item = next(item for item in window.navigation.findItems(
                            "*", Qt.MatchWildcard | Qt.MatchRecursive)
                            if window.navigation.ref_for(item) == ("domain", domain))
                        return QPointF(window.navigation.visualItemRect(item).center())

                group_drag = GroupDragEvent()
                window.navigation.dropEvent(group_drag)
                self.assertTrue(group_drag.accepted)
                self.assertEqual(window.groups.get(group_a)["domain"], domain)
                window.select_navigation(("domain_ungrouped", domain))
                self.assertEqual([row[SessionListModel.RefRole]
                                  for row in window.session_model.rows], [domain_ref])
                self.assertEqual(window.subject.text(), "Ungrouped in example-domain")
                window.change_view("sessions")
                window.select_navigation(("group", group_a))
                window.groups.set_annotation([f"agent:codex:{agent_id}"],
                                             "Own middleware r:maintainer o:/")
                window.refresh_list()
                agent_item = window.session_model.index_for_ref(agent_ref)
                self.assertIn("r:maintainer", agent_item.data(SessionListModel.TagsRole))
                self.assertEqual(agent_item.data(SessionListModel.OverseesRole), ["/"])
                window.save_appearance()
            finally:
                window.close()
            records[0]["key"] = "revived-agent-window"
            window = SessionLaunch([])
            try:
                self.assertTrue(window.groups.get(group_a)["collapsed"])
                self.assertEqual(window.navigation_ref(), ("group", group_a))
                self.assertEqual(window.group_for_ref(
                    ("terminal", "revived-agent-window")), group_a)
                self.assertEqual(set(window.groups.groups_for(["terminal:plain-window"])),
                                 {group_a, group_b})
                self.assertEqual(window.groups.domain_for(["terminal:domain-only-window"]),
                                 domain)
                data["terminal"].remove(records[1])
                window.refresh_inventory(force=True)
                window.change_view("plain")
                window.select_navigation(("group", group_b))
                placeholder = window.session_model.index_for_ref(
                    ("terminal", "plain-window"))
                self.assertTrue(placeholder.isValid())
                self.assertEqual(placeholder.data(SessionListModel.CardPresenceRole),
                                 "closed terminal")
            finally:
                window.close()

    def test_phase5_compact_navigation_and_saved_pane_widths(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                domain = window.groups.create_domain("example-domain")
                group = window.groups.create("reporting")
                window.groups.set_group_domain(group, domain)
                window.refresh_navigation()
                window.resize(800, 650)
                window.show()
                app.processEvents()
                self.assertFalse(window.navigation_frame.isVisible())
                self.assertTrue(window.navigate_button.isVisible())
                window.show_navigation_popup()
                app.processEvents()
                self.assertTrue(window.navigation_popup.isVisible())
                item = next(item for item in window.popup_navigation.findItems(
                    "*", Qt.MatchWildcard | Qt.MatchRecursive)
                    if window.popup_navigation.ref_for(item) == ("group", group))
                window.popup_navigation.setCurrentItem(item)
                self.assertEqual(window.navigation_ref(), ("group", group))
                window.resize(1120, 760)
                app.processEvents()
                self.assertTrue(window.navigation_frame.isVisible())
                window.splitter.setSizes([260, 400, 440])
                saved_widths = window.splitter.sizes()
                window.save_appearance()
            finally:
                window.close()
            window = SessionLaunch([])
            try:
                self.assertEqual(window.navigation_ref(), ("group", group))
                self.assertEqual(window._pane_widths, saved_widths)
            finally:
                window.close()

    def test_phase5_retired_date_and_inherited_domain_are_visible(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication.instance() or QApplication([])
        agent_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        row = {"id": agent_id, "agent": "claude", "cwd": "/tmp",
               "display_name": "Middleware", "project": "example-project"}
        key = f"agent:claude:{agent_id}"
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch.object(SessionLaunch, "query_agent_detail"), \
             patch.object(SessionLaunch, "query_agent_messages"):
            window = SessionLaunch([])
            try:
                domain = window.groups.create_domain("example-domain")
                group = window.groups.create("reporting")
                window.groups.set_group_domain(group, domain)
                window.groups.add_membership([key], group)
                window.groups.retired[key] = "2026-11-17T12:30:00+00:00"
                window.groups.save()
                window.agent_rows = [row]
                window.refresh_navigation()
                window.select_navigation(("retired", ""))
                self.assertEqual(window.keep_view, "set_aside")
                index = window.session_model.index_for_ref(("agent", agent_id))
                self.assertTrue(index.isValid())
                self.assertEqual(index.data(SessionListModel.RetiredDateRole), "2026-11-17")
                window.session_list.setCurrentIndex(index)
                self.assertEqual(window.retired_label.text(), "Set aside 2026-11-17")
                self.assertIn("2026-11-17", window.retired_label.toolTip())
                self.assertEqual(window.inherited_domains.text(),
                                 "Also in example-domain via reporting")
                self.assertEqual(window.retire_button.text(), "Keep")
                window.set_keep_view("kept")
                self.assertEqual(window.navigation_ref(), ("all", ""))
            finally:
                window.close()

    def test_keep_filter_and_bulk_change_preserve_memberships(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        ids = ("b999622d-d0d5-587b-ab0f-af3d7cab1b4e",
               "c721c33f-f89f-533e-83b2-65049fc05b99")
        rows = [{"id": session_id, "agent": "claude", "cwd": "/tmp",
                 "display_name": f"Agent {number}", "project": "example-project"}
                for number, session_id in enumerate(ids)]
        keys = [f"agent:claude:{session_id}" for session_id in ids]
        data = {"session": [], "terminal": [], "saved": [], "scan_status": "ok"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value=data), \
             patch("sessionlaunch.HotlistServer.start"), \
             patch.object(SessionLaunch, "refresh_agents"), \
             patch.object(SessionLaunch, "query_agent_detail"), \
             patch.object(SessionLaunch, "query_agent_messages"):
            window = SessionLaunch([])
            try:
                domain = window.groups.create_domain("example-domain")
                group = window.groups.create("repair")
                window.groups.set_group_domain(group, domain)
                for key in keys:
                    window.groups.add_membership([key], group)
                window.agent_rows = rows
                window.refresh_navigation()
                window.select_navigation(("group", group))
                self.assertEqual(window.session_model.rowCount(), 2)
                window.set_keep_for_identities([[keys[0]], [keys[1]]], False)
                self.assertEqual(window.session_model.rowCount(), 0)
                self.assertEqual([window.groups.groups_for([key]) for key in keys],
                                 [[group], [group]])
                window.set_keep_view("set_aside")
                self.assertEqual(window.session_model.rowCount(), 2)
                self.assertFalse(window.session_model.index(0, 0).data(
                    SessionListModel.KeptRole))
                window.set_keep_for_identities([[keys[0]], [keys[1]]], True)
                self.assertEqual(window.session_model.rowCount(), 0)
                window.set_keep_view("kept")
                self.assertEqual(window.session_model.rowCount(), 2)
                window.set_keep_view("all")
                window.show()
                app.processEvents()
                index = window.session_model.index(0, 0)
                first_ref = index.data(SessionListModel.RefRole)
                rect = window.session_list.visualRect(index)
                QTest.mouseMove(window.session_list.viewport(),
                                QPoint(rect.left() + 22, rect.top() + 18))
                self.assertEqual(window.session_list.viewport().cursor().shape(),
                                 Qt.PointingHandCursor)
                QTest.mouseMove(window.session_list.viewport(),
                                QPoint(rect.right() - 4, rect.bottom() - 4))
                self.assertNotEqual(window.session_list.viewport().cursor().shape(),
                                    Qt.PointingHandCursor)
                QTest.mouseClick(window.session_list.viewport(), Qt.LeftButton,
                                 Qt.NoModifier, QPoint(rect.left() + 22, rect.top() + 18))
                app.processEvents()
                self.assertTrue(window.groups.is_retired(window.group_keys_for_ref(first_ref)))
                self.assertEqual(window.session_model.rowCount(), 2)
                window.session_list.setCurrentIndex(window.session_model.index_for_ref(first_ref))
                window.keep_button.click()
                self.assertFalse(window.groups.is_retired(window.group_keys_for_ref(first_ref)))
                window.set_keep_for_identities([[keys[0]]], False)
                window.session_model.set_rows([(("agent", ids[0]), "Agent 0", "/tmp",
                                                "running", [], "", False)])
                running = window.session_model.index(0, 0)
                self.assertEqual(running.data(SessionListModel.CardPresenceRole), "running")
                self.assertFalse(running.data(SessionListModel.KeptRole))
                option = QStyleOptionViewItem()
                option.font = window.session_list.font()
                option.rect = QRect(0, 0, 355, running.data(Qt.SizeHintRole).height())
                self.assertEqual(window.session_list.itemDelegate().card_geometry(
                    option, running)["status_text"], "running")
            finally:
                window.close()

    def test_resize_cursor_resets_over_content(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        surface = ResizeSurface()
        surface.resize(200, 100)
        layout = QVBoxLayout(surface)
        child = QPushButton("Content")
        layout.addWidget(child)
        surface.show()
        try:
            app.processEvents()
            QTest.mouseMove(surface, QPoint(3, 50))
            app.processEvents()
            self.assertEqual(surface.cursor().shape(), Qt.SizeHorCursor)
            QTest.mouseMove(child, child.rect().center())
            app.processEvents()
            self.assertEqual(child.cursor().shape(), Qt.ArrowCursor)
        finally:
            surface.close()

    def test_status_and_repo_pill(self):
        self.assertEqual(display_status("live", "bash"), "idle")
        self.assertEqual(display_status("live", "-zsh"), "idle")
        self.assertEqual(display_status("live", "claude"), "running")
        self.assertEqual(display_status("dormant", "claude"), "saved")
        self.assertEqual(display_status("unlinked"), "saved")
        self.assertEqual(tag_label("repo:OpenPrinting/cups"), "<OpenPrinting/cups>")

    def test_last_active_keeps_datetime_and_relative_age(self):
        now = datetime(2026, 10, 12, 12, 41, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(format_last_active("2026-10-12 12:36", now),
                         "2026-10-12 12:36 EDT (5 minutes ago)")
        self.assertEqual(format_last_active("2026-10-12 12:46", now),
                         "2026-10-12 12:46 EDT (in 5 minutes)")
        self.assertEqual(format_last_active("2026-10-12T16:36:00Z", now),
                         "2026-10-12 12:36 EDT (5 minutes ago)")

    def test_su_chain_directory_and_independent_facets(self):
        terminal = {
            "state": "live", "pid": 4, "cwd": "", "pwd_env": "",
            "process": [
                {"pid": 4, "ppid": 3, "comm": "journalctl", "cwd": "", "pwd_env": ""},
                {"pid": 3, "ppid": 2, "comm": "bash", "cwd": "", "pwd_env": ""},
                {"pid": 2, "ppid": 1, "comm": "su", "cwd": "", "pwd_env": ""},
                {"pid": 1, "ppid": 0, "comm": "bash", "cwd": "/tmp/work", "pwd_env": ""},
            ],
        }
        self.assertEqual(terminal_directory(terminal),
                         ("/tmp/work", "Attached bash CWD (inferred)"))
        self.assertTrue(elevation_reason(terminal).startswith("Via su"))
        self.assertEqual(record_facets("live", "journalctl", True),
                         {"presence": "alive", "activity": "active", "access": "privileged"})
        self.assertEqual(record_facets("dormant", "journalctl", True),
                         {"presence": "saved", "activity": None, "access": None})

    def test_frozen_reader_shows_session_arguments_and_linked_live_process(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        saved = {
            "id": "frozen-test", "title": "printf", "directory": "/tmp",
            "command_argv": ["/usr/bin/printf", "%s\\n", "hello world"],
            "captured_at": "2026-10-12T12:00:00+00:00", "source_terminal_key": "old",
            "state": "live", "replay_policy": "run", "shell": "/usr/bin/bash",
            "shell_source": "observed",
        }
        terminal = {
            "key": "new", "state": "live", "frozen_id": saved["id"],
            "terminal": "xterm", "terminal_pid": 40, "host_application": "printf",
            "comm": "printf", "pid": 42, "pgid": 42, "sid": 41,
            "argv": ["/usr/bin/printf", "%s\\n", "hello world"],
            "pty": "/dev/pts/9", "cwd": "/tmp", "pwd_env": "/tmp",
            "window_id": "", "agent": "", "session_id": "", "uid": 1000,
            "euid": 1000, "processes": [],
        }
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value={
            "session": [], "terminal": [], "saved": [saved], "scan_status": "ok"
        }):
            window = SessionLaunch([])
            try:
                self.assertEqual(window.inventory_timer.interval(), 1000)
                window.change_view("frozen")
                row = next(index for index in range(window.session_model.rowCount())
                           if window.session_model.index(index, 0).data(SessionListModel.RefRole) == ("saved", saved["id"]))
                self.assertEqual(window.session_model.index(row, 0).data(
                    SessionListModel.CardPresenceRole), "frozen")
                window.session_list.setCurrentIndex(window.session_model.index(row, 0))
                app.processEvents()
                self.assertFalse(window.session_header.isHidden())
                self.assertTrue(window.live_header.isHidden())
                self.assertNotIn("Session UUID", window.session_values)
                self.assertNotIn("Directory", window.session_values)
                self.assertEqual(window.session_values["Project / CWD"], "/tmp")
                window.pending_links[saved["id"]] = 0
                with patch("inventory.update", return_value={
                    "session": [], "terminal": [terminal], "saved": [saved],
                    "scan_status": "ok", "changed": True
                }):
                    window.poll_pending_links()
                self.assertEqual(window.session_model.index_for_ref(("saved", saved["id"])).data(
                    SessionListModel.CardPresenceRole), "running")
                self.assertFalse(window.live_header.isHidden())
                self.assertFalse(window.pending_links)
                self.assertEqual(window.session_values["Arguments"], "'%s\\n' 'hello world'")
                self.assertEqual(window.live_values["Host PID"], 42)
                self.assertEqual(window.live_values["PTY slave"], "/dev/pts/9")
                self.assertTrue(window.live_fields.isHidden())
                window.set_processes([{"pid": 42, "ppid": 1, "pgid": 42, "sid": 41,
                                       "comm": "printf", "cwd": "/tmp"}])
                self.assertTrue(window.process_table.isHidden())
                window.attachment_toggle.click()
                self.assertFalse(window.live_fields.isHidden())
                self.assertFalse(window.process_table.isHidden())
                output = io.StringIO()
                with patch("sessionlaunch.subprocess.run", return_value=CompletedProcess(
                        ["wmctrl", "-lxp"], 1, "", "Cannot open display")), \
                     redirect_stdout(output):
                    window.report_unraisable_saved(saved)
                self.assertIn("| WARNING  | sessionlaunch.window - raise failed", output.getvalue())
                self.assertIn("pty=/dev/pts/9", output.getvalue())
                self.assertIn("wmctrl -lxp rc=1", output.getvalue())
                output = io.StringIO()
                with patch("sessionlaunch.subprocess.run", side_effect=CalledProcessError(
                        1, ["wmctrl", "-ia", "0x1"])), \
                     patch("sessionlaunch.QMessageBox.warning"), redirect_stdout(output):
                    self.assertFalse(window.act_window("0x1", "raise"))
                self.assertIn("| ERROR    | sessionlaunch.window - window action=raise",
                              output.getvalue())
                with patch("inventory.update", return_value={
                        "session": [], "terminal": [terminal], "saved": [saved],
                        "scan_status": "ok", "changed": False
                }), patch.object(window, "refresh_tags") as refresh_tags, \
                     patch.object(window, "refresh_list") as refresh_list:
                    window.refresh_inventory()
                    refresh_tags.assert_not_called()
                    refresh_list.assert_not_called()
            finally:
                window.close()

    def test_linked_agent_is_raised_without_duplicate_launch(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        agent = {"id": "2d7ac13b-e20b-5074-9fdb-88eaeee4d87d", "agent": "claude",
                 "cwd": "/tmp", "project": "cups", "display_name": "CUPS work",
                 "message_count": 86}
        terminal = {"key": "live-agent", "state": "live", "pty": "/dev/pts/5",
                    "agent": "claude", "session_id": agent["id"], "terminal": "terminal",
                    "terminal_pid": 40, "host_application": "claude", "comm": "claude",
                    "pid": 42, "pgid": 42, "sid": 41, "argv": ["claude"],
                    "cwd": "/tmp", "pwd_env": "/tmp", "window_id": "0x123",
                    "window_source": "wmctrl PID", "uid": 1000, "euid": 1000,
                    "processes": []}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value={
            "session": [], "terminal": [terminal], "saved": [], "scan_status": "ok"
        }), patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                with patch.object(window, "query_agent_detail"), \
                     patch.object(window, "query_agent_messages"):
                    window.change_view("sessions")
                    window.agent_rows = [agent]
                    window.agent_by_id = {agent["id"]: agent}
                    window.refresh_list()
                    agent_item = next(window.session_model.index(index, 0)
                                      for index in range(window.session_model.rowCount())
                                      if window.session_model.index(index, 0).data(SessionListModel.RefRole) ==
                                      ("agent", agent["id"]))
                    window.session_list.setCurrentIndex(agent_item)
                self.assertEqual(agent_item.data(0x0101), "running")
                self.assertEqual(window.live_values["PTY slave"], "/dev/pts/5")
                self.assertEqual(window.selected_live()["key"], "live-agent")
                self.assertIn("Freeze foreground command…",
                              [action.text() for action in window.agent_menu(
                                  ("agent", agent["id"])).actions()])
                with patch.object(window, "act_window", return_value=True), \
                     patch("sessionlaunch.restore.launch") as launch:
                    window.launch_selected()
                    launch.assert_not_called()
                window.show_agent_terminal()
                self.assertEqual(window.selected_ref(), ("agent", agent["id"]))
                self.assertTrue(window.attachment_toggle.isChecked())
            finally:
                window.close()

    def test_agent_revival_requires_confirmation_and_uses_systemd_backend(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        agent = {"id": "b999622d-d0d5-587b-ab0f-af3d7cab1b4e", "agent": "claude",
                 "cwd": "/tmp", "project": "stoplog", "display_name": "Stoplog"}
        with tempfile.TemporaryDirectory() as directory, \
             patch("sessionlaunch.GROUPS_FILE", Path(directory) / "groups.json"), \
             patch("sessionlaunch.APPEARANCE_FILE", Path(directory) / "appearance.json"), \
             patch("inventory.update", return_value={
            "session": [], "terminal": [], "saved": [], "scan_status": "ok"
        }), patch.object(SessionLaunch, "refresh_agents"):
            window = SessionLaunch([])
            try:
                with patch.object(window, "query_agent_detail"), \
                     patch.object(window, "query_agent_messages"):
                    window.change_view("sessions")
                    window.agent_rows = [agent]
                    window.agent_by_id = {agent["id"]: agent}
                    window.refresh_list()
                    agent_item = next(window.session_model.index(index, 0)
                                      for index in range(window.session_model.rowCount())
                                      if window.session_model.index(index, 0).data(SessionListModel.RefRole) ==
                                      ("agent", agent["id"]))
                    window.session_list.setCurrentIndex(agent_item)
                self.assertEqual(window.launch_button.text(), "Revive agent…")
                window.set_agent_messages([
                    {"role": "user", "timestamp": "2026-10-12T16:36:00Z",
                     "content": "find the right session " + "x" * 800},
                ])
                card = window.message_layout.itemAt(0).widget()
                self.assertIn("2026-10-12 12:36 EDT (", card.layout().itemAt(0).widget().text())
                body = card.layout().itemAt(1).widget()
                self.assertTrue(body.text().endswith("…"))
                card.layout().itemAt(2).widget().click()
                self.assertIn("x" * 800, body.text())
                with patch("sessionlaunch.QMessageBox.question", return_value=QMessageBox.No), \
                     patch("sessionlaunch.restore.launch") as launch:
                    window.launch_selected()
                    launch.assert_not_called()
                with patch("sessionlaunch.QMessageBox.question", return_value=QMessageBox.Yes) as ask, \
                     patch("sessionlaunch.install_rcfile"), \
                     patch("sessionlaunch.restore.launch") as launch:
                    launch.return_value.unit = "sessionlaunch-test.service"
                    window.launch_selected()
                    self.assertEqual(ask.call_args.args[0:2], (window, "Revive agent?"))
                    self.assertIn("claude --resume b999622d-d0d5-587b-ab0f-af3d7cab1b4e",
                                  ask.call_args.args[2])
                    self.assertEqual(launch.call_args.args[3],
                                     "claude --resume b999622d-d0d5-587b-ab0f-af3d7cab1b4e")
                    self.assertEqual(launch.call_args.kwargs["backend"], "systemd-user")
                    key = f"agent:claude:{agent['id']}"
                    self.assertIn(key, window.terminals)
                    window.terminals[key].window_id = "0x123"
                    window.show_agent(agent)
                    self.assertEqual(window.launch_button.text(), "Opening terminal…")
                    self.assertEqual(window.session_values["Live process"], "Terminal starting")
                    self.assertTrue(window.window_menu_button.isEnabled())
                    with patch.object(window, "act_window", return_value=True) as act:
                        self.assertTrue(window.selected_action("raise"))
                    act.assert_called_once_with("0x123", "raise")
            finally:
                window.close()

    def test_remote_hotlist_request_still_requires_gui_confirmation(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        row = {"id": "b999622d-d0d5-587b-ab0f-af3d7cab1b4e",
               "agent": "claude", "cwd": "/tmp", "title": "Example"}
        with patch("inventory.update", return_value={
            "session": [], "terminal": [], "saved": [], "scan_status": "ok"
        }), patch("sessionlaunch.HotlistServer.start"):
            window = SessionLaunch([])
            try:
                with patch("sessionlaunch.QMessageBox.question", return_value=QMessageBox.No) as ask, \
                     patch("sessionlaunch.restore.launch") as launch:
                    window.queue_hotlist_revives([row])
                    app.processEvents()
                    self.assertTrue(ask.called)
                    launch.assert_not_called()
                self.assertFalse(window.remote_revives)
            finally:
                window.close()

    def test_remote_revive_queue_accepts_more_while_first_confirmation_waits(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        rows = [{"id": f"00000000-0000-0000-0000-{index:012x}",
                 "agent": "claude", "cwd": "/tmp", "title": str(index)}
                for index in range(11)]
        with patch("inventory.update", return_value={
            "session": [], "terminal": [], "saved": [], "scan_status": "ok"
        }), patch("sessionlaunch.HotlistServer.start"):
            window = SessionLaunch([])
            seen = []

            def decline(_window, _title, message, *_args):
                seen.append(message)
                if len(seen) == 1:
                    window.queue_hotlist_revives([rows[-1]])
                return QMessageBox.No

            try:
                with patch("sessionlaunch.QMessageBox.question", side_effect=decline), \
                     patch("sessionlaunch.restore.launch") as launch:
                    window.queue_hotlist_revives(rows[:10])
                    for _ in range(30):
                        app.processEvents()
                    launch.assert_not_called()
                self.assertEqual(len(seen), 11)
                self.assertEqual(window.remote_revives, [])
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
