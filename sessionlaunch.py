#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in session_launch.gui."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("session_launch.gui", run_name="__main__")
else:
    sys.modules[__name__] = importlib.import_module("session_launch.gui")
