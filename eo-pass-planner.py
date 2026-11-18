#!/usr/bin/env python3
"""Run eo-pass-planner from this folder without installing it.

Needs only Python with numpy and PyYAML:

    python eo-pass-planner.py --help
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from eo_pass_planner import main  # noqa: E402

main()
