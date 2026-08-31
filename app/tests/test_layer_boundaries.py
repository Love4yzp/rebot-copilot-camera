"""Layer boundaries as machine locks, not prose.

The four boundaries ``AGENTS.md`` states as prose — ``executor`` never imports
the latch, ``ActionContext`` has no arm handle, ``api`` never reaches the
validators directly, ``arm`` never implements kinematics — are enforced by AST
import scanning here. A new file in a forbidden layer, or a forbidden import
added to an existing one, fails loudly at review time instead of slipping
through. The ``estop``-never-disables and motion-gate tests already use this
technique; this file closes the gap they leave open.

The rule for each layer is the *minimum* that keeps the architecture honest,
not a maximally strict graph. The deliberate front doors — ``api/gate`` and
``api/estop`` importing ``safety`` (the latch), ``api/plugins`` importing
``actions`` (the registry) — are allowed: those touch the latch and the
registry, not the validators. What is banned everywhere in ``api/`` is importing
``safety.kinematics`` or ``actions.validate``, the modules that carry the
``validate_*`` functions every motion check must route through
``Controller.preflight_*``.

Also locks the state-machine invariant that the published coarse ``mode`` and
the ``resting`` flag never contradict each other: a resting arm reads
``mode == "idle"`` with ``resting`` true, and an engaged stop clears rest — so
a client that reads only ``mode`` is never told ``idle`` while the arm has no
torque. This is the ``"the UI never guesses where the arm is"`` rule applied to
the wire shape, not just the renderer.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from backend.actions import ActionContext, InlineRunner, ShutterProvider
from backend.arm import SimArm
from backend.core import Broadcaster, Controller
from backend.safety import LatchSource, SafetyLatch
from backend.shutter import SimShutter

BACKEND = Path(__file__).resolve().parent.parent / "backend"


# ── helpers ───────────────────────────────────────────────────────────────────


def _resolved_imports(file: Path) -> set[str]:
    """Absolute module strings a file imports.

    ``from ..safety import kinematics`` (a submodule) yields
    ``backend.safety.kinematics``; ``from ..safety import SafetyLatch`` (a
    class from the package) yields ``backend.safety``. That distinction is what
    lets the api rule ban the kinematics *module* while leaving the latch
    *class* legal.
    """
    pkg_parts = ("backend",) + file.relative_to(BACKEND).parent.parts
    tree = ast.parse(file.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.level:
                # level 1 = this package, level 2 = parent, etc. Drop that many
                # trailing parts from the file's package to get the base.
                base_parts = pkg_parts[: max(0, len(pkg_parts) - (node.level - 1))]
                mod_parts = tuple(node.module.split(".")) if node.module else ()
                base = ".".join(base_parts + mod_parts)
            else:
                base = node.module
            out.add(base)
            for a in node.names:
                # An imported name may be a submodule of the base. Yield that
                # too so `from ..safety import kinematics` is caught as the
                # kinematics module.
                out.add(f"{base}.{a.name}")
    return out


def _layer_files(subdir: str) -> list[Path]:
    return sorted((BACKEND / subdir).rglob("*.py"))


# ── boundary 1: executor never imports the latch or safety ─────────────────


def test_executor_does_not_import_the_latch_or_safety():
    """The executor is pure block-traversal. It must not reach for the latch:
    the control loop decides when a stop aborts a run (by calling
    ``executor.abort()``), so the executor structurally cannot decide to
    recover on its own. A stray ``from ..safety import`` here would reopen that
    path. ``events`` (semantic names) is allowed — the executor emits, it just
    cannot subscribe."""
    executor = BACKEND / "core" / "executor.py"
    bad = {m for m in _resolved_imports(executor) if m.startswith("backend.safety")}
    assert not bad, (
        "executor.py must not import from safety — the latch is checked in the "
        "control loop, never in the executor; otherwise the executor could "
        f"decide to recover on its own: {bad}"
    )


# ── boundary 2: ActionContext has no arm/latch/store handle ──────────────────


def test_action_context_carries_no_arm_or_latch_handle():
    """A provider that cannot reach the arm, the latch or the stores cannot be
    the reason any of them did something surprising. Locking the field *set*
    (not just a banned-name check) means adding any field forces a deliberate
    decision here — which is the point of ``"small on purpose"``."""
    allowed = {
        "routine_id",
        "routine_name",
        "waypoint_index",
        "waypoint_note",
        "joints",
        "emit",
    }
    actual = {f.name for f in dataclasses.fields(ActionContext)}
    assert actual == allowed, (
        "ActionContext grew a field. The context is small on purpose — a "
        "provider can read the pose it started at and emit an event, and "
        "nothing else. Adding a handle to the arm, the latch or a store here "
        "lets a plugin reach around the motion gate. If this is deliberate, "
        f"update the allowed set: new={actual - allowed}"
    )


# ── boundary 3: api never imports the validators directly ───────────────────


def test_api_does_not_import_the_validators_directly():
    """``api/*`` talks to the controller and the stores, never to
    ``safety.kinematics`` or ``actions.validate``. The three deliberate front
    doors — ``gate``/``estop`` importing ``safety`` (the latch) and ``plugins``
    importing ``actions`` (the registry) — are not the validators and stay
    legal."""
    banned = {"backend.safety.kinematics", "backend.actions.validate"}
    offenders: list[str] = []
    for path in _layer_files("api"):
        hit = _resolved_imports(path) & banned
        if hit:
            offenders.append(f"{path.relative_to(BACKEND)}: {sorted(hit)}")
    assert not offenders, (
        "api/ must not import safety.kinematics or actions.validate — those "
        "validators are reached through Controller.preflight_*, not directly. "
        f"{offenders}"
    )


# ── boundary 4: arm layer never implements kinematics ───────────────────────


def test_arm_layer_does_not_import_kinematics():
    """FK / IK / gravity / trajectory planning live in the upstream submodule
    and are reached through ``assets``, not re-implemented in ``arm/``. A direct
    ``import pinocchio`` here would be the start of a parallel implementation;
    the dynamics model is passed in, never imported at this layer."""
    offenders: list[str] = []
    for path in _layer_files("arm"):
        hit = {m for m in _resolved_imports(path) if m == "pinocchio" or m.startswith("pinocchio.")}
        if hit:
            offenders.append(f"{path.relative_to(BACKEND)}: {sorted(hit)}")
    assert not offenders, (
        "arm/ must not import pinocchio — kinematics/dynamics are called via "
        "the upstream submodule through assets, never implemented here: "
        f"{offenders}"
    )


# ── invariant: the published mode and resting never contradict ───────────────


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def rig() -> "tuple[Controller, _Clock, list]":
    clock = _Clock()
    arm = SimArm(("joint1", "joint2"), clock=clock)
    arm.connect()
    shutter = SimShutter()
    latch = SafetyLatch(clock=clock)
    bc = Broadcaster()
    published: list = []
    bc.publish = published.append  # type: ignore[method-assign]
    # Inline runner: a fake clock and real worker threads must never race. The
    # loop-stays-free property is the subject of test_action_runner.py.
    controller = Controller(
        arm=arm, shutter=shutter, latch=latch, broadcaster=bc,
        clock=clock, expected_period_s=0.01,
        actions=InlineRunner([ShutterProvider(shutter)]),
    )
    return controller, clock, published


def _assert_invariant(controller: Controller):
    """A resting arm must read ``idle`` (rest clears on any latch/teach/play),
    and an engaged stop must clear rest and report ``estop`` — so ``mode``
    alone is enough to know the motors are commanded."""
    if controller._resting:
        assert controller.mode == "idle", (
            "a resting arm has no torque; reporting anything but 'idle' would "
            "tell a mode-only client the motors are holding when they are not"
        )
    if controller.latch.is_latched:
        assert not controller._resting, "an engaged stop clears rest — torque-less and stopped cannot coexist"
        assert controller.mode == "estop"


def test_mode_and_resting_stay_consistent_across_every_transition(rig):
    """Drive the loop through idle → rest → wake → teach → stop → clear →
    teach, and after every tick assert the published ``mode`` does not
    contradict ``resting``. This is the wire-shape half of the rule that the
    renderer never guesses where the arm is."""
    controller, clock, published = rig

    def tick() -> None:
        clock.now += 0.01
        arm = controller.arm
        arm.step(0.01)
        controller.tick()
        _assert_invariant(controller)
        # The published state is what a client actually sees.
        state = next(m for m in published if m["type"] == "state")
        published.clear()
        rest = state["data"]["resting"]
        mode = state["data"]["mode"]
        if rest:
            assert mode == "idle", (
                f"published mode={mode!r} with resting=True — a client reading "
                "only mode would miss that the arm has no torque"
            )

    # idle
    tick()

    # enter rest at the zero pose (arm starts at zero)
    controller.set_resting(True)
    for _ in range(3):
        tick()

    # a motion command wakes rest
    controller.set_resting(False)
    tick()

    # enter teaching (locked, no float yet)
    controller.set_teaching(True)
    for _ in range(3):
        tick()

    # engage the stop mid-teach — must clear teaching AND never leave resting
    controller.latch.engage("operator hit the bar", LatchSource.UI)
    for _ in range(3):
        tick()

    # clear the stop — drops into teaching, not rest
    controller.latch.clear()
    for _ in range(3):
        tick()

    # back to idle, then rest again, then a latch while resting
    controller.set_teaching(False)
    tick()
    controller.set_resting(True)
    for _ in range(3):
        tick()
    controller.latch.engage("watchdog", LatchSource.WATCHDOG)
    for _ in range(3):
        tick()
