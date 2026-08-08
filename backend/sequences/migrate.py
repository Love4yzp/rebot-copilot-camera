"""v1 → v2 migration: waypoint-list Routines become poses plus block sequences.

Runs once at startup, before the server comes up: if the sequences directory
is empty and the routines directory has files, every stored v1 routine is
rewritten as a v2 sequence and its waypoints become library poses. The v1 files
are never deleted — they are the backup.

The mapping, per routine:

- each waypoint becomes a :class:`Pose` (named by its note, else 位姿 N)
- each waypoint becomes a hold block whose duration is the old settle time
  plus the sum of its actions' estimates, with the actions pinned as markers
  at ``settle_s`` and onward — the same place in the block they fired in the
  old ACTING phase
- between waypoints, a transition whose duration is the *next* waypoint's
  ``duration_s`` (that number always meant "the move to this waypoint") and
  whose easing is ease_in_out, the UI's default

A v1 ``sleep`` action is pure time on the plan ruler, not an event, so it
folds into the hold's duration rather than becoming a marker. (A marker kind
with no provider behind it would fail the run at execute time.)

Estimates are new — v1 never estimated action durations. A shutter burst is
``count`` frames at the UI's instant-trigger estimate plus the configured
gaps; anything else gets a conservative default rather than asking the
provider, because migration runs headless at startup and the board may not be
attached.

Written as a pure function over injected stores so the tests can point it at
fixture JSON.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import (
    DEFAULT_EASING,
    EventMarker,
    HoldBlock,
    Pose,
    Sequence,
    TransitionBlock,
)
from .store import PoseStore, SequenceStore

log = logging.getLogger(__name__)

#: Per-frame estimate for a migrated shutter marker. Matches the estimate the
#: UI puts on an instant trigger, so migrated sequences display like new ones.
SHUTTER_FRAME_ESTIMATE_S = 0.3

#: Estimate for a migrated plugin action. Conservative on purpose: it is v1's
#: default per-action timeout, i.e. the longest the host would ever wait. The
#: provider is not asked — migration runs at startup, possibly with the board
#: unplugged, and the estimate is a display hint, never a deadline.
PLUGIN_ACTION_ESTIMATE_S = 5.0


@dataclass
class MigrationReport:
    routines_migrated: int = 0
    poses_created: int = 0
    #: Files that would not parse; logged and left alone.
    files_skipped: list[str] = field(default_factory=list)
    #: Action types with no v2 home. v1 only ever wrote shutter/sleep/plugin,
    #: so reaching this means a hand-edited file.
    actions_dropped: int = 0


def _shutter_estimate(params: dict) -> float:
    count = int(params.get("count", 1))
    interval = float(params.get("interval_s", 0.0))
    return count * SHUTTER_FRAME_ESTIMATE_S + max(0, count - 1) * interval


def migrate_routine(
    data: dict,
    pose_store: PoseStore,
    sequence_store: SequenceStore,
) -> tuple[Sequence, int]:
    """Migrate one parsed v1 routine JSON. Returns the sequence and how many
    actions were dropped. Stores are written as a side effect."""
    poses: list[Pose] = []
    blocks = []
    dropped = 0

    waypoints = data.get("waypoints", [])
    # All of a routine's poses inherit its created_at for archival truth, but a
    # shared timestamp ties the library's creation-order sort and leaves the
    # order to random filename globbing. Bump each by a millisecond so waypoint
    # order survives as library order.
    base_created = data.get("created_at", time.time())
    for index, wp in enumerate(waypoints):
        note = (wp.get("note") or "").strip()
        pose = pose_store.save(
            Pose(
                name=note or f"位姿 {index + 1}",
                joints=wp.get("joints", {}),
                created_at=base_created + index * 0.001,
                updated_at=data.get("updated_at", time.time()),
            )
        )
        poses.append(pose)

        settle_s = wp.get("settle_ms", 300) / 1000.0
        markers: list[EventMarker] = []
        offset = settle_s
        for action in wp.get("actions", []):
            kind = action.get("type")
            if kind == "shutter":
                params = {
                    "count": action.get("count", 1),
                    "interval_s": action.get("interval_s", 0.0),
                    "focus_first": action.get("focus_first", True),
                }
                markers.append(
                    EventMarker(kind="shutter", params=params, at=offset,
                                estimate_s=_shutter_estimate(params))
                )
                offset += markers[-1].estimate_s
            elif kind == "sleep":
                # Pure time, not an event: fold it into the hold.
                offset += action.get("duration_s", 0.0)
            elif kind == "plugin":
                markers.append(
                    EventMarker(
                        kind=action.get("provider", "unknown"),
                        params=action.get("params", {}),
                        at=offset,
                        estimate_s=PLUGIN_ACTION_ESTIMATE_S,
                    )
                )
                offset += PLUGIN_ACTION_ESTIMATE_S
            else:
                log.warning("dropping unknown action type %r during migration", kind)
                dropped += 1

        blocks.append(HoldBlock(pose_id=pose.id, duration_s=offset, markers=markers))

        if index < len(waypoints) - 1:
            # The next waypoint's duration_s was always "the move to it".
            blocks.append(
                TransitionBlock(
                    duration_s=waypoints[index + 1].get("duration_s", 2.0),
                    easing=DEFAULT_EASING,  # type: ignore[arg-type]
                )
            )

    sequence = sequence_store.save(
        Sequence(
            id=data.get("id") or Sequence().id,
            name=data.get("name", " migrated"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            blocks=blocks,
        )
    )
    return sequence, dropped


def migrate_routines(
    routines_dir: Path,
    pose_store: PoseStore,
    sequence_store: SequenceStore,
) -> MigrationReport:
    """Migrate every readable v1 routine file in ``routines_dir``."""
    report = MigrationReport()
    for path in sorted(Path(routines_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sequence, dropped = migrate_routine(data, pose_store, sequence_store)
        except Exception:
            log.exception("skipping unmigratable routine file: %s", path)
            report.files_skipped.append(path.name)
            continue
        report.routines_migrated += 1
        report.poses_created += sum(1 for b in sequence.blocks if isinstance(b, HoldBlock))
        report.actions_dropped += dropped
        log.info(
            "migrated routine %s (%s) -> sequence with %d blocks",
            sequence.id, sequence.name, len(sequence.blocks),
        )
    return report


def maybe_migrate(
    routines_dir: Path,
    sequences_dir: Path,
    pose_store: PoseStore,
    sequence_store: SequenceStore,
) -> MigrationReport | None:
    """The startup hook. Migrates only when the v2 library is empty and there
    is v1 data — anything else means a human is already managing the files."""
    sequences_dir = Path(sequences_dir)
    routines_dir = Path(routines_dir)
    if any(sequences_dir.glob("*.json")):
        return None
    if not any(routines_dir.glob("*.json")):
        return None
    report = migrate_routines(routines_dir, pose_store, sequence_store)
    log.info(
        "v1->v2 migration: %d routines, %d poses, %d files skipped, %d actions dropped "
        "(originals left in %s)",
        report.routines_migrated, report.poses_created,
        len(report.files_skipped), report.actions_dropped, routines_dir,
    )
    return report
