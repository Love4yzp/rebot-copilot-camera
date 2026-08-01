"""Runtime configuration.

Only values that are genuinely deployment-dependent live here. Notably absent:
joint limits. The previous generation hand-copied them into a config file, where
they drifted from the hardware and were trusted anyway; here they are read from
the URDF (plan commit #33).
"""

from __future__ import annotations

import os
from pathlib import Path

from .assets import REPO_ROOT

#: One JSON per routine. Gitignored — this is operator data, not source.
ROUTINES_DIR = Path(os.environ.get("REBOT_ROUTINES_DIR", REPO_ROOT / "routines"))

#: Localhost only. Remote access goes through an SSH tunnel, as before.
HOST = os.environ.get("REBOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("REBOT_PORT", "18790"))
