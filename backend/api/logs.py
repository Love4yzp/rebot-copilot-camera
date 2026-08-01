"""Service logs, for the UI's log drawer.

The operator is usually on the far end of an SSH tunnel with a browser, not a
terminal. Surfacing the journal in the UI is the difference between "the arm
stopped and I do not know why" and reading the watchdog's reason.

Reads journalctl rather than a log file, because the service runs under systemd
and has no log file. Off the device — during development on a laptop — there is
no journal at all, which is reported plainly instead of looking like an empty
log.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from fastapi import APIRouter, Query
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["logs"])

SERVICE = "rebot-copilot-camera"
TIMEOUT_S = 5.0


class LogResponse(BaseModel):
    available: bool
    lines: list[str]
    note: str | None = None


@router.get("", response_model=LogResponse)
def get_logs(lines: int = Query(default=200, ge=1, le=2000)) -> LogResponse:
    """Recent service log lines, newest last."""
    if shutil.which("journalctl") is None:
        return LogResponse(
            available=False,
            lines=[],
            note="journalctl not present — this is normal off the R2x",
        )

    try:
        result = subprocess.run(
            ["journalctl", "-u", SERVICE, "-n", str(lines), "--output=cat", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("journalctl failed: %s", exc)
        return LogResponse(available=False, lines=[], note=f"journalctl failed: {exc}")

    if result.returncode != 0:
        # The usual cause is the service account not being in systemd-journal,
        # which produces an empty result rather than an error the operator can
        # act on. Say which it is.
        return LogResponse(
            available=False,
            lines=[],
            note=(
                f"journalctl exited {result.returncode}: {result.stderr.strip() or 'no output'}. "
                "Check the service account is in the systemd-journal group."
            ),
        )

    return LogResponse(
        available=True,
        lines=[line for line in result.stdout.splitlines() if line.strip()],
    )
