"""Compatibility entry point; implementation lives in session_launch.claude_peers."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("session_launch.claude_peers", run_name="__main__")
else:
    sys.modules[__name__] = importlib.import_module("session_launch.claude_peers")
