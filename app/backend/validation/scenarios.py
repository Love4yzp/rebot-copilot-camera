"""Deterministic headless playback scenarios for optional MuJoCo validation.

Scenarios use the public Controller and SequenceExecutor path.  The partial
target case is intentionally direct ArmSession coverage because the defect is
below the sequence layer.  Commands and measured plant samples are emitted as
separate CSV rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .. import assets
from ..actions import InlineRunner
from ..arm.session import ArmSession
from ..core.broadcaster import Broadcaster
from ..core.controller import Controller
from ..safety import SafetyLatch, Watchdog, WatchdogConfig
from ..safety.kinematics import validate_pose
from ..sequences import EventMarker, HoldBlock, Pose, Sequence, TransitionBlock
from .physics import ALL_JOINTS, JOINTS, PhysicsPlant, RecordingTransport, load_model

APP_PERIOD = 0.01
PHYSICS_DT = 0.001
SEED = 0


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@dataclass
class Rig:
    clock: Clock
    transport: RecordingTransport
    plant: PhysicsPlant
    controller: Controller
    latch: SafetyLatch
    scenario: str
    command_cursor: int = 0
    command_rows: list[dict[str, Any]] = field(default_factory=list)

    def capture_commands(self) -> None:
        for command in self.transport.commands[self.command_cursor:]:
            self.command_rows.append({
                "scenario": self.scenario, "kind": "command", "t": command.t,
                "q": dict(command.q_des), "v": dict(command.v_des),
                "kp": dict(command.kp), "kd": dict(command.kd),
                "tau": dict(command.tau_ff),
            })
        self.command_cursor = len(self.transport.commands)

    def physics_to(self, target: float) -> None:
        while self.clock.t + PHYSICS_DT <= target + 1e-12:
            self.clock.t += PHYSICS_DT
            self.plant.step(PHYSICS_DT)

    def app_tick(self, at: float | None = None) -> None:
        if at is not None:
            self.physics_to(at)
            self.clock.t = at
        self.controller.tick()
        self.capture_commands()

    def close(self) -> None:
        self.plant.bundle.close()


def _all_target(value: float = 0.0) -> dict[str, float]:
    return {name: (value if name == "joint2" else 0.0) for name in ALL_JOINTS}


def _pose(name: str, joint2: float = 0.0) -> Pose:
    joints = _all_target(joint2)
    errors = validate_pose({n: joints[n] for n in JOINTS})
    if errors:
        raise RuntimeError(f"scenario pose {name!r} failed Pinocchio safety: {errors}")
    return Pose(name=name, joints=joints)


def _rig(q0: dict[str, float] | None = None) -> Rig:
    clock = Clock()
    transport = RecordingTransport(q0=q0, clock=clock)
    session = ArmSession(clock=clock, transport=transport)
    bundle = load_model()
    plant = PhysicsPlant(bundle, transport)
    latch = SafetyLatch(clock=clock)
    watchdog = Watchdog(latch, clock=clock, config=WatchdogConfig(late_tick_grace_s=0.0))
    controller = Controller(
        arm=session, shutter=object(), latch=latch,
        broadcaster=Broadcaster(), clock=clock, actions=InlineRunner([]),
        watchdog=watchdog,
    )
    return Rig(clock, transport, plant, controller, latch, "")


def _run_until(rig: Rig, predicate: Callable[[], bool], end: float) -> None:
    while rig.clock.t < end - 1e-12 and not predicate():
        now = rig.clock.t
        rig.app_tick(now)
        rig.physics_to(now + APP_PERIOD)
        rig.clock.t = now + APP_PERIOD


def _peak_command_speed(rows: list[dict[str, Any]], joint: str = "joint2") -> float:
    pairs = [(r["t"], r["q"].get(joint)) for r in rows if r["kind"] == "command" and joint in r["q"]]
    return max((abs((q1 - q0) / (t1 - t0)) for (t0, q0), (t1, q1) in zip(pairs, pairs[1:]) if t1 > t0), default=0.0)


def _run_later_transition() -> tuple[dict[str, Any], Rig]:
    rig = _rig()
    rig.scenario = "later_short_transition"
    a, b = _pose("zero"), _pose("later", 0.8)
    sequence = Sequence(name="later-short-transition", blocks=[
        HoldBlock(pose_id=a.id, duration_s=0.0),
        TransitionBlock(duration_s=0.5), HoldBlock(pose_id=b.id, duration_s=0.0),
    ])
    rig.controller.play(sequence, {a.id: a, b.id: b}, source="validation")
    _run_until(rig, lambda: rig.controller.executor is not None and rig.controller.executor.is_finished, 20.0)
    executor = rig.controller.executor
    final_q = float(rig.transport.get_state()[0][1])
    return {"scenario": "later_short_transition", "peak_command_speed_joint2_rad_s": _peak_command_speed(rig.command_rows), "nonzero_desired_velocity": any(abs(row.get("v", {}).get("joint2", 0.0)) > 1e-9 for row in rig.command_rows), "phase": executor.phase.value if executor else None, "final_arrival_error_rad": abs(final_q - 0.8)}, rig


def _run_gap(gap: float) -> tuple[dict[str, Any], Rig]:
    rig = _rig()
    rig.scenario = f"gap_{gap:g}s"
    a, b = _pose("zero"), _pose("gap", 0.8)
    sequence = Sequence(name=f"gap-{gap}", blocks=[
        HoldBlock(pose_id=a.id, duration_s=0.0), TransitionBlock(duration_s=2.0),
        HoldBlock(pose_id=b.id, duration_s=0.0),
    ])
    rig.controller.play(sequence, {a.id: a, b.id: b}, source="validation")
    _run_until(rig, lambda: rig.controller.executor is not None and rig.controller.executor.phase.value == "transition", 1.0)
    mid_start = rig.clock.t
    _run_until(rig, lambda: rig.clock.t >= mid_start + 0.5, mid_start + 1.0)
    before = float(next(r["q"]["joint2"] for r in reversed(rig.command_rows) if r["kind"] == "command" and "joint2" in r["q"]))
    # Preserve the historical fault injection semantics: the omitted control
    # interval is measured from the preceding app tick, while the plant keeps
    # stepping for ``gap`` seconds. Thus the observed tick gap is gap + one
    # normal application period.
    gap_start = rig.clock.t - APP_PERIOD
    recovery_cursor = len(rig.command_rows)
    rig.physics_to(rig.clock.t + gap)
    rig.app_tick()
    actual_tick_gap = rig.clock.t - gap_start
    after = float(next(r["q"]["joint2"] for r in reversed(rig.command_rows) if r["kind"] == "command" and "joint2" in r["q"]))
    # A fixed watchdog may latch on this first recovery observation; the
    # follow-up tick verifies that a legacy sustained-lateness implementation
    # cannot sneak a second motion command through.
    recovery_t = rig.clock.t + APP_PERIOD
    rig.physics_to(recovery_t)
    rig.clock.t = recovery_t
    rig.app_tick()
    executor = rig.controller.executor
    recovery_rows = [row for row in rig.command_rows[recovery_cursor:] if "joint2" in row.get("q", {})]
    recovery = recovery_rows[0] if recovery_rows else {}
    recovery_q = recovery.get("q", {}).get("joint2")
    recovery_v = recovery.get("v", {}).get("joint2")
    return {"scenario": f"gap_{gap:g}s", "gap_s": gap, "actual_tick_gap_s": actual_tick_gap, "clock_plant_delta_s": rig.clock.t - rig.plant.t, "reference_before": before, "reference_after": after, "reference_jump_rad": after - before, "latch_absorbed": rig.latch.is_latched, "recovery_command_q": recovery_q, "recovery_command_v": recovery_v, "recovery_is_hold": bool(rig.latch.is_latched and recovery_q is not None and abs(recovery_v or 0.0) <= 1e-9), "executor_terminated": executor is None, "post_gap_phase": executor.phase.value if executor else "aborted", "phase": executor.phase.value if executor else None}, rig


def _run_wait() -> tuple[dict[str, Any], Rig]:
    rig = _rig()
    rig.scenario = "wait_transition_0.5_pause_5s"
    a, b = _pose("wait-start"), _pose("wait-end", 0.8)
    sequence = Sequence(name="wait-five-seconds", blocks=[
        HoldBlock(pose_id=a.id, duration_s=0.0),
        TransitionBlock(duration_s=2.0, markers=[EventMarker(kind="wait", at=0.5)]),
        HoldBlock(pose_id=b.id, duration_s=0.0),
    ])
    rig.controller.play(sequence, {a.id: a, b.id: b}, source="validation")
    _run_until(rig, lambda: rig.controller.executor is not None and rig.controller.executor.is_waiting, 30.0)
    executor = rig.controller.executor
    phase_at_wait = executor.phase.value if executor else None
    entry_rows = [row for row in rig.command_rows if row["kind"] == "command" and any(joint in row.get("q", {}) for joint in JOINTS)]
    wait_entry_hold_target = dict(entry_rows[-1]["q"]) if entry_rows else {}
    wait_start = rig.clock.t
    commands_at_wait = len(rig.transport.commands)
    wait_end = wait_start + 5.0
    wait_tick_gaps: list[float] = []
    wait_command_ticks = 0
    wait_hold_targets: list[dict[str, float]] = []
    previous_tick = wait_start
    while rig.clock.t < wait_end - 1e-12:
        now = min(rig.clock.t + APP_PERIOD, wait_end)
        rig.physics_to(now)
        rig.clock.t = now
        wait_tick_gaps.append(now - previous_tick)
        previous_tick = now
        before_tick = len(rig.command_rows)
        rig.app_tick()
        tick_rows = [row for row in rig.command_rows[before_tick:] if row["kind"] == "command" and any(joint in row.get("q", {}) for joint in JOINTS)]
        if tick_rows:
            wait_command_ticks += 1
            wait_hold_targets.append(dict(tick_rows[-1]["q"]))
    held_commands = len(rig.transport.commands) - commands_at_wait
    resumed = rig.controller.resume()
    _run_until(rig, lambda: executor is not None and executor.is_finished, rig.clock.t + 30.0)
    final_q = float(rig.transport.get_state()[0][1])
    stable = bool(wait_hold_targets) and all(target == wait_hold_targets[0] for target in wait_hold_targets[1:])
    return {"scenario": "wait_transition_0.5_pause_5s", "phase_at_wait": phase_at_wait, "wait_start_s": wait_start, "wait_end_s": wait_end, "wait_tick_count": len(wait_tick_gaps), "wait_tick_gaps_s": wait_tick_gaps, "wait_command_ticks": wait_command_ticks, "wait_entry_hold_target": wait_entry_hold_target, "wait_hold_targets": wait_hold_targets, "wait_target_stable": stable, "commands_during_wait": held_commands, "resume_accepted": resumed, "phase_after_resume": executor.phase.value if executor else None, "error": executor.error if executor else None, "final_arrival_error_rad": abs(final_q - 0.8)}, rig


def _run_partial_noise() -> tuple[dict[str, Any], None]:
    clock = Clock()
    transport = RecordingTransport(clock=clock)
    session = ArmSession(clock=clock, transport=transport)
    # This is the one intentionally partial command: it isolates the
    # ArmSession feedback-noise defect from sequence target completeness.
    session.move_to({"joint2": 0.5}, 2.0)
    first = next(c for c in reversed(transport.commands) if "joint2" in c.q_des)
    q, v, tau = transport.get_state()
    q[2] = 0.2
    transport.set_feedback(q, v, tau)
    clock.t = 0.01
    session.move_to({"joint2": 0.5}, 2.0)
    second = next(c for c in reversed(transport.commands) if "joint2" in c.q_des)
    return {"scenario": "partial_feedback_noise", "joint3_reference_change_rad": second.q_des["joint3"] - first.q_des["joint3"]}, None


def _run_retarget() -> tuple[dict[str, Any], Rig]:
    rig = _rig()
    rig.scenario = "midmove_retarget"
    a, b, c = _pose("zero"), _pose("first", 0.8), _pose("retarget", 0.2)
    sequence = Sequence(name="retarget", blocks=[HoldBlock(pose_id=a.id, duration_s=0.0), TransitionBlock(duration_s=4.0), HoldBlock(pose_id=b.id, duration_s=0.0)])
    rig.controller.play(sequence, {a.id: a, b.id: b}, source="validation")
    _run_until(rig, lambda: rig.controller.executor is not None and rig.controller.executor.phase.value == "transition" and rig.clock.t >= 1.0, 2.0)
    old_row = next(r for r in reversed(rig.command_rows) if r["kind"] == "command" and "joint2" in r["q"])
    old, old_v = old_row["q"]["joint2"], old_row["v"].get("joint2", 0.0)
    old_sample = rig.plant.samples[-1]
    rig.controller.move_joints(c.joints, 2.0, source="validation")
    rig.capture_commands()
    new_row = next(r for r in reversed(rig.command_rows) if r["kind"] == "command" and "joint2" in r["q"])
    new, new_v = new_row["q"]["joint2"], new_row["v"].get("joint2", 0.0)
    boundary_t = rig.clock.t + APP_PERIOD
    rig.physics_to(boundary_t)
    rig.clock.t = boundary_t
    rig.app_tick()
    new_sample = rig.plant.samples[-1]
    sampled_rows = [row for row in rig.command_rows if row["kind"] == "command" and "joint2" in row["q"]]
    sampled = sampled_rows[-1]
    return {"scenario": "midmove_retarget", "old_reference": old, "new_reference": new, "reference_jump_rad": new - old, "old_reference_velocity_rad_s": old_v, "new_reference_velocity_rad_s": new_v, "velocity_jump_rad_s": new_v - old_v, "sampled_reference_q_delta_rad": sampled["q"]["joint2"] - new, "sampled_reference_v_delta_rad_s": sampled["v"].get("joint2", 0.0) - new_v, "sampled_dt_s": sampled["t"] - new_row["t"], "measured_boundary_q_delta_rad": float(new_sample["q"][1] - old_sample["q"][1]), "measured_boundary_v_delta_rad_s": float(new_sample["v"][1] - old_sample["v"][1])}, rig


def _source_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    paths = {"session": root / "backend/arm/session.py", "executor": root / "backend/core/executor.py", "controller": root / "backend/core/controller.py", "watchdog": root / "backend/safety/watchdog.py"}
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}


def run_scenarios() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    rigs: list[Rig] = []
    scenarios = ((_run_later_transition, "later_short_transition"), (lambda: _run_gap(0.1), "gap_0.1s"), (lambda: _run_gap(1.0), "gap_1s"), (_run_wait, "wait_transition_0.5_pause_5s"), (_run_partial_noise, "partial_feedback_noise"), (_run_retarget, "midmove_retarget"))
    for fn, name in scenarios:
        try:
            result, rig = fn()
        except Exception as exc:  # Keep batch evidence reviewable.
            result, rig = {"scenario": name, "status": "failure", "error_type": type(exc).__name__, "error": str(exc)}, None
        results.append(result)
        if rig is not None:
            rigs.append(rig)
    rows: list[dict[str, Any]] = []
    physical: list[dict[str, Any]] = []
    for rig in rigs:
        finite = all(np.isfinite(sample["q"] + sample["v"] + sample["tau"]).all() for sample in rig.plant.samples)
        saturation = sum(any(sample.get("saturated", ())) for sample in rig.plant.samples)
        physical.append({"scenario": rig.scenario, "finite_state": finite, "sample_count": len(rig.plant.samples), "max_abs_q_rad": float(max((np.max(np.abs(sample["q"])) for sample in rig.plant.samples), default=0.0)), "max_abs_v_rad_s": float(max((np.max(np.abs(sample["v"])) for sample in rig.plant.samples), default=0.0)), "max_abs_tau_nm": float(max((np.max(np.abs(sample["tau"])) for sample in rig.plant.samples), default=0.0)), "torque_saturation_samples": saturation, "contact_samples": sum(int(sample.get("ncon", 0) > 0) for sample in rig.plant.samples), "max_contacts": max((int(sample.get("ncon", 0)) for sample in rig.plant.samples), default=0)})
        rows.extend(rig.command_rows)
        rows.extend({"scenario": rig.scenario, "kind": "measured", "t": sample["t"], "q": dict(zip(JOINTS, sample["q"])), "v": dict(zip(JOINTS, sample["v"])), "tau": dict(zip(JOINTS, sample["tau"]))} for sample in rig.plant.samples)
        rig.close()
    return {"mode": "controller-sequenceexecutor-mujoco", "scenarios": results, "physical_runs": physical}, rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    names = list(JOINTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["scenario", "kind", "t", *[f"q_{n}" for n in names], *[f"v_{n}" for n in names], *[f"tau_{n}" for n in names]])
        for row in rows:
            writer.writerow([row["scenario"], row["kind"], row["t"], *[row.get("q", {}).get(n, "") for n in names], *[row.get("v", {}).get(n, "") for n in names], *[row.get("tau", {}).get(n, "") for n in names]])


def check_payload(payload: dict[str, Any]) -> list[str]:
    by_name = {result["scenario"]: result for result in payload["scenarios"]}
    failures: list[str] = []
    expected_results = {"later_short_transition", "gap_0.1s", "gap_1s", "wait_transition_0.5_pause_5s", "partial_feedback_noise", "midmove_retarget"}
    missing_results = expected_results - set(by_name)
    if missing_results:
        failures.append(f"scenario result set is incomplete: {sorted(missing_results)}")
        return failures
    expected_integrated = {"later_short_transition", "gap_0.1s", "gap_1s", "wait_transition_0.5_pause_5s", "midmove_retarget"}
    physical_by_name = {item.get("scenario"): item for item in payload.get("physical_runs", [])}
    if set(physical_by_name) != expected_integrated:
        failures.append("physical run scenario set is incomplete")
    for name in expected_integrated:
        run = physical_by_name.get(name)
        if not run or run.get("sample_count", 0) <= 0 or not run.get("finite_state", False):
            failures.append(f"{name} has missing or non-finite physical samples")
    later = by_name["later_short_transition"]
    if later.get("peak_command_speed_joint2_rad_s", float("inf")) > 0.25 + 1e-6:
        failures.append("later transition exceeds speed limit")
    if later.get("phase") != "done" or later.get("final_arrival_error_rad", float("inf")) > 0.01:
        failures.append("later transition did not arrive")
    if not later.get("nonzero_desired_velocity", False):
        failures.append("motion did not send velocity feedforward")
    if abs(by_name["partial_feedback_noise"].get("joint3_reference_change_rad", float("inf"))) > 1e-9:
        failures.append("partial target restarted on feedback noise")
    wait = by_name["wait_transition_0.5_pause_5s"]
    if wait.get("phase_at_wait") != "wait":
        failures.append("WAIT did not enter the wait phase")
    if wait.get("wait_tick_count", 0) <= 0 or wait.get("wait_command_ticks", 0) != wait.get("wait_tick_count", 0):
        failures.append("WAIT did not stream one command per control tick")
    wait_entry_target = wait.get("wait_entry_hold_target")
    wait_targets = wait.get("wait_hold_targets")
    if not isinstance(wait_entry_target, dict) or not isinstance(wait_targets, list) or not wait_targets:
        failures.append("WAIT is missing the entry or first-tick hold target")
    else:
        missing_joints = [joint for joint in JOINTS if joint not in wait_entry_target]
        if missing_joints or any(not isinstance(target, dict) or any(joint not in target for joint in JOINTS) for target in wait_targets):
            failures.append("WAIT hold target is missing arm joint commands")
        elif any(target.get(joint) != wait_entry_target.get(joint) for target in wait_targets for joint in JOINTS):
            failures.append("WAIT entry and pause-window hold targets differ")
    if not wait.get("wait_target_stable", False):
        failures.append("WAIT hold target drifted")
    if any(gap <= 0 or gap > APP_PERIOD * 1.5 for gap in wait.get("wait_tick_gaps_s", [])):
        failures.append("WAIT control tick gap exceeded the expected period")
    if wait.get("phase_after_resume") != "done" or wait.get("final_arrival_error_rad", float("inf")) > 0.01:
        failures.append("WAIT resume did not arrive at the target")
    for name in ("gap_0.1s", "gap_1s"):
        if abs(by_name[name].get("reference_jump_rad", float("inf"))) > 0.1:
            failures.append(f"{name} reference jumped")
        if abs(by_name[name].get("actual_tick_gap_s", float("inf")) - (by_name[name].get("gap_s", float("nan")) + APP_PERIOD)) > 1e-6:
            failures.append(f"{name} injected gap did not match actual tick gap")
    if not by_name["gap_1s"].get("latch_absorbed") or not by_name["gap_1s"].get("recovery_is_hold") or by_name["gap_1s"].get("post_gap_phase") not in ("aborted", None):
        failures.append("1s gap did not enter holding safe lock")
    retarget = by_name["midmove_retarget"]
    required_retarget_fields = ("reference_jump_rad", "velocity_jump_rad_s", "sampled_reference_q_delta_rad", "sampled_reference_v_delta_rad_s", "sampled_dt_s")
    missing_retarget_fields = [field for field in required_retarget_fields if field not in retarget or not isinstance(retarget[field], (int, float)) or isinstance(retarget[field], bool) or not math.isfinite(retarget[field])]
    if missing_retarget_fields:
        failures.append(f"midmove retarget has missing or non-finite fields: {missing_retarget_fields}")
        return failures
    if abs(retarget["reference_jump_rad"]) > 1e-8:
        failures.append("midmove retarget reference jump exceeded tolerance")
    if abs(retarget["velocity_jump_rad_s"]) > 1e-8:
        failures.append("midmove retarget velocity jump exceeded tolerance")
    sample_dt = max(retarget["sampled_dt_s"], APP_PERIOD)
    if abs(retarget["sampled_reference_q_delta_rad"]) > 0.25 * sample_dt:
        failures.append("retarget reference q exceeded speed bound")
    if abs(retarget["sampled_reference_v_delta_rad_s"]) > 0.5 * sample_dt:
        failures.append("retarget reference v exceeded acceleration bound")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run optional headless motion validation scenarios")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload, rows = run_scenarios()
    payload.update({"git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "source_hashes": _source_hashes(), "settings": {"seed": SEED, "app_period_s": APP_PERIOD, "physics_dt_s": PHYSICS_DT, "integrator": "implicitfast", "urdf_sha256": hashlib.sha256(assets.urdf_path().read_bytes()).hexdigest(), "python": platform.python_version(), "mujoco": importlib.metadata.version("mujoco")}})
    csv_path = args.csv or args.output.with_suffix(".csv")
    _write_csv(csv_path, rows)
    payload["artifacts"] = {"csv": str(csv_path), "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(), "row_count": len(rows)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    if args.check:
        failures = check_payload(payload)
        if failures:
            raise SystemExit("validation failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
