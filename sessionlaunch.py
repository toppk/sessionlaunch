#!/usr/bin/env python3
"""Run the checkout GUI without installing an editable package."""
import os
import runpy
import sys
from pathlib import Path

source = str(Path(__file__).resolve().parent / "src")
sys.path.insert(0, source)
# The GUI starts helper subprocesses with ``python -m session_launch``.
os.environ["PYTHONPATH"] = os.pathsep.join(
    part for part in (source, os.environ.get("PYTHONPATH", "")) if part)
runpy.run_module("session_launch.gui", run_name="__main__")
