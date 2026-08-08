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
#: Kept as the v1 migration source; new writes go to the v2 stores below.
ROUTINES_DIR = Path(os.environ.get("REBOT_ROUTINES_DIR", REPO_ROOT / "routines"))

#: The v2 libraries: pose library, block/marker sequences, sequence templates.
#: Same treatment as routines/ — operator data, one JSON per document.
POSES_DIR = Path(os.environ.get("REBOT_POSES_DIR", REPO_ROOT / "poses"))
SEQUENCES_DIR = Path(os.environ.get("REBOT_SEQUENCES_DIR", REPO_ROOT / "sequences"))
TEMPLATES_DIR = Path(os.environ.get("REBOT_TEMPLATES_DIR", REPO_ROOT / "templates"))

#: Localhost only. Remote access goes through an SSH tunnel, as before.
HOST = os.environ.get("REBOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("REBOT_PORT", "18790"))

#: The shutter board. udev gives the XIAO this stable name
#: (deploy/99-rebot-usb.rules): it and the USB2CAN bridge are both generic CDC
#: devices, so raw /dev/ttyACM* numbering swaps with plug order, and a shutter
#: driver pointed at the CAN bridge looks exactly like a dead camera.
SHUTTER_PORT = os.environ.get("REBOT_SHUTTER_PORT", "/dev/rebot-shutter")
SHUTTER_BAUD = int(os.environ.get("REBOT_SHUTTER_BAUD", "115200"))
