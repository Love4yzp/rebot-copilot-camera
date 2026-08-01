"""Routine persistence: one JSON file per routine.

Carried over from the previous generation, where it earned its keep. A file per
routine means a corrupt or half-written file costs one routine rather than the
whole library, routines can be copied between machines with ``scp``, and a
human can read and fix one in an editor at 2am on a shoot.

Writes go through a temp file plus ``os.replace``, so a crash mid-write leaves
either the old routine or the new one, never a truncated file that parses as
valid JSON with half the waypoints missing.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from .models import Routine, RoutineSummary

log = logging.getLogger(__name__)

#: Routine ids come from uuid4 hex, but ids also land in filesystem paths, so
#: anything that could escape the store directory is rejected outright.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RoutineNotFound(KeyError):
    def __init__(self, rid: str) -> None:
        super().__init__(rid)
        self.rid = rid


class RoutineStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ── paths ────────────────────────────────────────────────────────────────

    def _path(self, rid: str) -> Path:
        if not _SAFE_ID.match(rid):
            raise RoutineNotFound(rid)
        return self.root / f"{rid}.json"

    # ── read ─────────────────────────────────────────────────────────────────

    def exists(self, rid: str) -> bool:
        return self._path(rid).exists()

    def get(self, rid: str) -> Routine:
        path = self._path(rid)
        if not path.exists():
            raise RoutineNotFound(rid)
        return Routine.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[RoutineSummary]:
        """Summaries, newest first.

        A single unreadable file is logged and skipped rather than raising. The
        alternative is that one bad file makes the whole library inaccessible
        from the UI, which is exactly when the operator most needs to reach the
        other routines.
        """
        summaries: list[RoutineSummary] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                routine = Routine.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                log.exception("skipping unreadable routine file: %s", path)
                continue
            summaries.append(RoutineSummary.of(routine))
        return sorted(summaries, key=lambda s: s.updated_at, reverse=True)

    # ── write ────────────────────────────────────────────────────────────────

    def save(self, routine: Routine) -> Routine:
        """Persist atomically. Returns the routine for call chaining."""
        path = self._path(routine.id)
        payload = routine.model_dump_json(indent=2)

        # Temp file in the same directory: os.replace is only atomic within a
        # filesystem, and /tmp is frequently a different one.
        fd, tmp_name = tempfile.mkstemp(dir=self.root, prefix=f".{routine.id}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return routine

    def delete(self, rid: str) -> None:
        path = self._path(rid)
        if not path.exists():
            raise RoutineNotFound(rid)
        path.unlink()
