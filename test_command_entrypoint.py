"""The installed command keeps inspection separate from revival requests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from session_launch import cli, hotlist_cli, hotlist_ipc


class CommandEntrypointTest(unittest.TestCase):
    def test_describe_is_generated_from_dispatch_table(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["describe", "--format", "json"]), 0)
        rows = json.loads(output.getvalue())
        self.assertEqual({row["name"] for row in rows}, set(cli.COMMANDS))
        self.assertEqual(next(row["effect"] for row in rows
                              if row["name"] == "request-revive"),
                         "request-gui-confirmation")

    def test_hotlist_can_only_request_revival_through_explicit_command(self):
        session_id = "b999622d-d0d5-587b-ab0f-af3d7cab1b4e"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hotlist.toml"
            path.write_text(f'[[session]]\nagent = "claude"\nid = "{session_id}"\n'
                            'repo = "/tmp/example"\ntitle = "Example"\n')
            options = ["--hotlist", str(path), "--id", session_id]
            with patch.object(hotlist_cli, "live_agents", return_value=[]), \
                 patch.object(hotlist_ipc, "request_revive", return_value=1) as send, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["hotlist", *options]), 0)
                send.assert_not_called()
                with redirect_stderr(io.StringIO()) as errors, \
                     self.assertRaises(SystemExit) as refused:
                    cli.main(["hotlist", *options, "--revive"])
                self.assertEqual(refused.exception.code, 2)
                self.assertIn("sessionlaunch request-revive", errors.getvalue())
                send.assert_not_called()
                self.assertEqual(cli.main(["request-revive", *options]), 0)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(send.call_args.args[0][0]["id"], session_id)


if __name__ == "__main__":
    unittest.main()
