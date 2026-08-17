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

#: The v2 libraries: pose library, block/marker sequences, sequence templates.
#: Operator data, one JSON per document, gitignored.
POSES_DIR = Path(os.environ.get("REBOT_POSES_DIR", REPO_ROOT / "poses"))
SEQUENCES_DIR = Path(os.environ.get("REBOT_SEQUENCES_DIR", REPO_ROOT / "sequences"))
TEMPLATES_DIR = Path(os.environ.get("REBOT_TEMPLATES_DIR", REPO_ROOT / "templates"))

#: Drop-in plugins, one folder each with a plugin.json. Gitignored like the
#: operator data — it is content the user added, not this repo's source — but
#: unlike the stores it syncs with device.sh push, because it is code whose
#: source of truth is the development machine.
PLUGINS_DIR = Path(os.environ.get("REBOT_PLUGINS_DIR", REPO_ROOT / "plugins"))

#: Operator-calibrated tuning (payload profile, float gains, thresholds),
#: written only by the tuning panel's explicit save. Kept out of
#: config/rebotarm_rs.yaml: that file is a commented upstream fork, and a
#: YAML round-trip would strip every comment.
TUNING_FILE = Path(os.environ.get("REBOT_TUNING_FILE", REPO_ROOT / "config" / "tuning.yaml"))

#: Localhost only. Remote access goes through an SSH tunnel, as before.
HOST = os.environ.get("REBOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("REBOT_PORT", "18790"))

#: The shutter board. udev gives the XIAO this stable name
#: (deploy/99-rebot-usb.rules): it and the USB2CAN bridge are both generic CDC
#: devices, so raw /dev/ttyACM* numbering swaps with plug order, and a shutter
#: driver pointed at the CAN bridge looks exactly like a dead camera.
SHUTTER_PORT = os.environ.get("REBOT_SHUTTER_PORT", "/dev/rebot-shutter")
SHUTTER_BAUD = int(os.environ.get("REBOT_SHUTTER_BAUD", "115200"))
