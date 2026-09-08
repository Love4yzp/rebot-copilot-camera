"""Optional physics playback scenarios exercise the production command path."""

import importlib.util
from copy import deepcopy

import pytest

if importlib.util.find_spec("mujoco") is None:
    pytest.skip("install the optional physics extra", allow_module_level=True)

from backend.validation.scenarios import check_payload, run_scenarios


def _passing_payload() -> dict:
    wait_target = {f"joint{i}": 0.0 for i in range(1, 7)}
    scenarios = [
        {"scenario": "later_short_transition", "peak_command_speed_joint2_rad_s": 0.1, "phase": "done", "final_arrival_error_rad": 0.0, "nonzero_desired_velocity": True},
        {"scenario": "gap_0.1s", "gap_s": 0.1, "actual_tick_gap_s": 0.11, "reference_jump_rad": 0.0},
        {"scenario": "gap_1s", "gap_s": 1.0, "actual_tick_gap_s": 1.01, "reference_jump_rad": 0.0, "latch_absorbed": True, "recovery_is_hold": True, "post_gap_phase": "aborted"},
        {"scenario": "wait_transition_0.5_pause_5s", "phase_at_wait": "wait", "wait_tick_count": 5, "wait_command_ticks": 5, "wait_entry_hold_target": wait_target, "wait_hold_targets": [wait_target.copy() for _ in range(5)], "wait_target_stable": True, "wait_tick_gaps_s": [0.01] * 5, "commands_during_wait": 5, "phase_after_resume": "done", "final_arrival_error_rad": 0.0},
        {"scenario": "partial_feedback_noise", "joint3_reference_change_rad": 0.0},
        {"scenario": "midmove_retarget", "reference_jump_rad": 0.0, "velocity_jump_rad_s": 0.0, "sampled_dt_s": 0.01, "sampled_reference_q_delta_rad": 0.0, "sampled_reference_v_delta_rad_s": 0.0},
    ]
    physical = [{"scenario": name, "sample_count": 1, "finite_state": True} for name in ("later_short_transition", "gap_0.1s", "gap_1s", "wait_transition_0.5_pause_5s", "midmove_retarget")]
    return {"scenarios": scenarios, "physical_runs": physical}


def test_scenarios_use_separate_command_and_measured_records():
    payload, rows = run_scenarios()
    names = {result["scenario"] for result in payload["scenarios"]}
    assert names == {
        "later_short_transition", "gap_0.1s", "gap_1s",
        "wait_transition_0.5_pause_5s", "partial_feedback_noise", "midmove_retarget",
    }
    assert any(row["kind"] == "command" for row in rows)
    assert any(row["kind"] == "measured" for row in rows)
    assert payload["mode"] == "controller-sequenceexecutor-mujoco"


def test_integrated_scenarios_pin_fixed_motion_contracts():
    payload, _rows = run_scenarios()
    assert check_payload(payload) == []


@pytest.mark.parametrize("mutation, expected", [
    (lambda p: p.update(physical_runs=[]), "physical run scenario set"),
    (lambda p: p["physical_runs"].pop(), "physical run scenario set"),
    (lambda p: p["physical_runs"][0].update(sample_count=0), "missing or non-finite"),
    (lambda p: p["scenarios"].pop(), "scenario result set"),
    (lambda p: p["scenarios"][3].update(wait_command_ticks=4), "one command per control tick"),
    (lambda p: p["scenarios"][3].update(wait_target_stable=False), "hold target drifted"),
    (lambda p: p["scenarios"][5].update(reference_jump_rad=10.0), "reference jump"),
    (lambda p: p["scenarios"][5].update(velocity_jump_rad_s=10.0), "velocity jump"),
    (lambda p: p["scenarios"][5].pop("reference_jump_rad"), "missing or non-finite fields"),
    (lambda p: p["scenarios"][5].update(reference_jump_rad=float("nan")), "missing or non-finite fields"),
])
def test_checker_rejects_incomplete_or_inconsistent_payloads(mutation, expected):
    payload = deepcopy(_passing_payload())
    mutation(payload)
    failures = check_payload(payload)
    assert any(expected in failure for failure in failures), failures
