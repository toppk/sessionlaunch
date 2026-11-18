"""Manual links stay attached to one observed agent process."""

import tempfile
import unittest
from pathlib import Path

import manual_links


class ManualLinksTest(unittest.TestCase):
    def test_link_round_trip_and_process_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manual_links.json"
            link = {"terminal_key": "window", "provider": "claude",
                    "session_id": "b999622d-d0d5-587b-ab0f-af3d7cab1b4e",
                    "agent_pid": 42, "agent_start_ticks": 1234,
                    "cwd": "/tmp", "moved_metadata": True}
            manual_links.save({"window": link}, path)
            loaded = manual_links.load(path)
            self.assertEqual(loaded["window"], link)
            terminal = {"key": "window", "state": "live", "agent": "claude",
                        "agent_pid": 42, "agent_start_ticks": 1234,
                        "agent_cwd": "/tmp", "session_id": "",
                        "session_id_source": ""}
            manual_links.apply([terminal], loaded)
            self.assertEqual(terminal["session_id"], link["session_id"])
            self.assertEqual(terminal["session_id_source"], "manual")
            later = {**terminal, "agent_pid": 43, "session_id": "", "session_id_source": ""}
            manual_links.apply([later], loaded)
            self.assertEqual(later["session_id"], "")
            reused = {**terminal, "agent_start_ticks": 5678,
                      "session_id": "", "session_id_source": ""}
            manual_links.apply([reused], loaded)
            self.assertEqual(reused["session_id"], "")
            other = {**terminal, "session_id": "c721c33f-f89f-533e-83b2-65049fc05b99",
                     "session_id_source": "argv"}
            manual_links.apply([other], loaded)
            self.assertEqual(other["session_id_source"], "argv")


if __name__ == "__main__":
    unittest.main()
