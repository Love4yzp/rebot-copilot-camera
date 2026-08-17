"""First-boot demo data: the four-station shoot the dev mock shows.

``./dev.sh sim`` opens on a populated library — four poses, the 四方位拍摄
sequence and the 四方位 template — because the mock seeds them. The real stores
start empty, so ``./dev.sh prod --sim`` used to open on a blank library and a
visitor pressing play got nothing to watch. This closes that gap: ``main()``
plants the same demo into empty stores, so the full stack demos like the mock.

Safety parity with anything an operator builds: every pose passes
:func:`~backend.safety.kinematics.validate_pose` (limits + self-collision) and
the sequence passes `validate_sequence` (poses plus straight-line paths), and
the blocks go through `normalize` exactly like a PATCHed sequence. Seeding
skips those checks would plant data the write API itself would refuse — the
demo must never be the one document that dodged validation.

Markers are the built-in `wait` and the built-in `shutter` provider only.
The mock's record/fill-light markers are presentation decorations with no
provider behind them, and a marker without a provider is exactly what the
write-time check exists to refuse.

Seeded once per deployment: a marker file records that the demo was planted,
so an operator who empties the library on purpose does not see it resurrect
on the next restart (delete the marker to get it back). `REBOT_SEED_DEMO=0`
disables seeding entirely.

The pose offsets mirror the mock's — both were corrected together to the
validated set after the backend's own checks refused the originals.
"""

from __future__ import annotations

import logging

from .. import assets
from ..safety.kinematics import validate_sequence
from .models import EventMarker, HoldBlock, Pose, SeqTemplate, Sequence, TransitionBlock
from .normalize import normalize
from .store import PoseStore, SequenceStore, TemplateStore

log = logging.getLogger(__name__)

#: Marker file next to the poses, written once the demo has been planted. The
#: stores glob only *.json, so it is invisible to them; it exists so that
#: "delete everything" sticks across restarts.
SEED_MARKER = ".seeded-demo"

#: Demo poses: name and joint offsets from the all-zero pose. Mirrored by
#: the mock (frontend/mock/state.ts seedPoses) and pinned by the
#: `seeded-library` contract case — ids, angles, blocks and the template all
#: change together or the contract goes red.
#:
#: The mock's original angles were chosen for the 3D view, and the real
#: backend refused them: joint3 was negative against a [0, π] URDF limit, and
#: the folded elbow self-collided. This set was re-derived inside the URDF
#: limits and passes validate_pose / validate_sequence, including the
#: straight-line paths from the zero pose — a demo the arm can actually play.
#:
#: Joints absent from the arm's joint list (the gripper, when it is off the
#: bus) drop out — a pose carrying a joint the arm does not have would fail
#: at move time, not at save time.
_POSE_SPECS: list[tuple[str, str, dict[str, float]]] = [
    ("demo-pose-front", "正面", {"joint1": 0.0, "joint2": 0.35, "joint3": 0.3, "joint4": 0.0, "joint5": 0.1, "joint6": 0.0, "gripper": 0.02}),
    ("demo-pose-right45", "右45°", {"joint1": 0.9, "joint2": 0.45, "joint3": 0.5, "joint4": 0.0, "joint5": 0.0, "joint6": 0.25, "gripper": 0.02}),
    ("demo-pose-side", "侧面", {"joint1": 1.5, "joint2": 0.3, "joint3": 0.25, "joint4": 0.0, "joint5": -0.2, "joint6": 0.5, "gripper": 0.02}),
    ("demo-pose-top", "俯拍", {"joint1": 0.15, "joint2": 0.9, "joint3": 0.5, "joint4": 0.3, "joint5": 0.4, "joint6": 0.0, "gripper": 0.02}),
]


def _shutter(at: float) -> EventMarker:
    return EventMarker(
        kind="shutter",
        params={"count": 1, "interval_s": 1.0, "focus_first": True},
        at=at,
        estimate_s=0.3,
    )


def _wait(at: float) -> EventMarker:
    return EventMarker(kind="wait", params={}, at=at, estimate_s=0.0)


def demo_poses(joint_names: list[str] | None = None) -> list[Pose]:
    """The four demo poses, keyed by the joints this deployment actually has."""
    names = joint_names if joint_names is not None else assets.joint_names()
    out: list[Pose] = []
    for pose_id, label, offsets in _POSE_SPECS:
        joints = {name: float(offsets.get(name, 0.0)) for name in names}
        out.append(Pose(id=pose_id, name=label, joints=joints))
    return out


def demo_sequence(poses: list[Pose]) -> Sequence:
    """The 四方位拍摄 sequence, mock parity minus the decoration markers.

    The wait marker at t=8 s suspends the run until the operator taps 继续 —
    same beat the mock demos.
    """
    front, right, side, top = [p.id for p in poses]
    blocks = normalize(
        [
            HoldBlock(pose_id=front, duration_s=3.0),
            TransitionBlock(duration_s=2.0, easing="ease_in_out"),
            HoldBlock(
                pose_id=right,
                duration_s=5.0,
                markers=[_shutter(2.0), _wait(3.0), _shutter(4.0)],
            ),
            TransitionBlock(duration_s=1.5, easing="linear"),
            HoldBlock(pose_id=side, duration_s=3.0),
            TransitionBlock(duration_s=2.0, easing="ease_in_out"),
            HoldBlock(pose_id=top, duration_s=4.0),
        ]
    )
    return Sequence(id="demo-seq-four", name="四方位拍摄", blocks=blocks)


def demo_template() -> SeqTemplate:
    """The four-station recipe: 3 s hold + one shutter per station."""
    recipe: list = []
    for slot in range(1, 5):
        if slot > 1:
            recipe.append(TransitionBlock(duration_s=2.0, easing="ease_in_out"))
        recipe.append(
            HoldBlock(pose_id=f"slot:{slot}", duration_s=3.0, markers=[_shutter(2.0)])
        )
    return SeqTemplate(id="demo-tpl-four", name="四方位", station_count=4, recipe=normalize(recipe))


def seed_demo_if_empty(
    pose_store: PoseStore,
    sequence_store: SequenceStore,
    template_store: TemplateStore,
    *,
    enabled: bool = True,
    joint_names: list[str] | None = None,
) -> bool:
    """Plant the demo into empty stores, once per deployment.

    Returns True when it seeded. Refuses whenever the stores are not all
    empty, the deployment was seeded before, seeding is disabled, or the demo
    data would not pass the same validation the write API applies — a broken
    demo must refuse to plant itself, not become the one unvalidated document.
    """
    if not enabled:
        return False
    marker = pose_store.root / SEED_MARKER
    if marker.exists():
        return False
    if pose_store.list() or sequence_store.list() or template_store.list():
        return False

    poses = demo_poses(joint_names)
    problems = validate_sequence([p.joints for p in poses])
    if problems:
        log.error("demo data failed validation; not seeding: %s", "; ".join(problems))
        return False

    for pose in poses:
        pose_store.save(pose)
    sequence_store.save(demo_sequence(poses))
    template_store.save(demo_template())
    marker.write_text(
        "demo library seeded on first boot; delete this file to re-seed an empty library",
        encoding="utf-8",
    )
    log.info("seeded the demo library: %d poses, 四方位拍摄 sequence, 四方位 template", len(poses))
    return True
