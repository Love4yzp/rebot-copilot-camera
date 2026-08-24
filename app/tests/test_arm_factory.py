"""Arm selection: --sim is explicit; a dead bus does not pretend to be live."""

import logging

import pytest

from backend.arm import ArmUnavailable, SimArm, create_arm


def test_force_sim_never_touches_the_real_arm(monkeypatch):
    """--sim has to work on a machine with no CAN stack at all, so the real
    session must not even be imported."""

    def explode(*args, **kwargs):
        raise AssertionError("ArmSession was constructed despite --sim")

    monkeypatch.setattr("backend.arm.session.ArmSession", explode, raising=False)

    arm, simulated = create_arm(force_sim=True)
    assert isinstance(arm, SimArm)
    assert simulated is True


def test_refuses_to_start_when_the_real_arm_is_unavailable(monkeypatch, caplog):
    class Unavailable:
        def __init__(self, *args, **kwargs):
            raise OSError("no such CAN device: can0")

    monkeypatch.setattr("backend.arm.session.ArmSession", Unavailable, raising=False)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ArmUnavailable, match="can0"):
            create_arm()
    assert "refusing to start" in caplog.text


def test_uses_the_real_arm_when_it_connects(monkeypatch):
    connected = []

    class Working:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            connected.append(True)

    monkeypatch.setattr("backend.arm.session.ArmSession", Working, raising=False)

    arm, simulated = create_arm()
    assert simulated is False
    assert connected == [True]
    assert not isinstance(arm, SimArm)


def test_the_simulated_arm_reports_the_hardware_joint_set():
    arm, _ = create_arm(force_sim=True)
    assert list(arm.joint_names) == [
        "joint1",
        "joint2",
        "joint3",
        "joint4",
        "joint5",
        "joint6",
        "gripper",
    ]
