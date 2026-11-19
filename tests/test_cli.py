"""Installed CLI effects and the explicit agent revival path."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from session_launch import cli, revive_cli


SESSION_ID = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"


class CommandEntrypointTest(unittest.TestCase):
    def test_describe_matches_dispatch_table(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["describe", "--format", "json"]), 0)
        rows = json.loads(output.getvalue())
        self.assertEqual({row["name"] for row in rows}, set(cli.COMMANDS))
        self.assertNotIn("hotlist", cli.COMMANDS)
        self.assertNotIn("request-revive", cli.COMMANDS)
        self.assertEqual(next(row["effect"] for row in rows if row["name"] == "revive"),
                         "launch-after-confirmation")

    def test_revive_preview_and_confirmation_use_service(self):
        with tempfile.TemporaryDirectory() as directory:
            session = {"agent": "claude", "id": SESSION_ID, "cwd": directory}
            replies = [
                {"data": {"session": session}},
                {"settings": {"default_terminal": "xterm",
                              "launch_backend": "systemd-user"}},
                {"launch_id": "launch-test"},
            ]
            with patch.object(revive_cli, "request", side_effect=replies) as service, \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(["revive", "claude", SESSION_ID, "--dry-run"]), 0)
                self.assertEqual(service.call_count, 2)
                self.assertIn(f"Command: claude --resume {SESSION_ID}", output.getvalue())
            with patch.object(revive_cli, "request", side_effect=replies) as service, \
                 patch.object(revive_cli.sys, "stdin") as stdin, \
                 redirect_stdout(io.StringIO()):
                stdin.isatty.return_value = False
                with self.assertRaises(SystemExit) as refused:
                    cli.main(["revive", "claude", SESSION_ID])
                self.assertEqual(refused.exception.code, 2)
                self.assertEqual(service.call_count, 2)
            with patch.object(revive_cli, "request", side_effect=replies) as service, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["revive", "claude", SESSION_ID, "--yes"]), 0)
                self.assertEqual(service.call_count, 3)
                launch = service.call_args
                self.assertEqual(launch.args, ("launch_terminal",))
                self.assertEqual(launch.kwargs["identity"], f"agent:claude:{SESSION_ID}")
                self.assertEqual(launch.kwargs["command"], f"claude --resume {SESSION_ID}")

    def test_revive_rejects_mismatched_agentsview_identity(self):
        with patch.object(revive_cli, "request", return_value={
                "data": {"session": {"agent": "codex", "id": SESSION_ID, "cwd": "/tmp"}}}) as service, \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as refused:
                cli.main(["revive", "claude", SESSION_ID, "--yes"])
        self.assertEqual(refused.exception.code, 1)
        service.assert_called_once()


if __name__ == "__main__":
    unittest.main()
