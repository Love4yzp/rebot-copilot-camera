"""Actuator and dynamics calls exposed without leaking upstream types."""

from __future__ import annotations

import numpy as np


def create_actuator(hardware_yaml: str) -> object:
    from reBotArm_control_py.actuator.rebotarm import RebotArm

    return RebotArm(hardware_yaml)


def load_dynamics(urdf_path: str) -> tuple[object, object]:
    from reBotArm_control_py.dynamics.robot_model import create_data, load_dynamics_model

    model = load_dynamics_model(urdf_path)
    return model, create_data(model)


def compute_gravity(model: object, q: np.ndarray, data: object) -> np.ndarray:
    from reBotArm_control_py.dynamics.inverse_dynamics import compute_generalized_gravity

    return compute_generalized_gravity(model, q, data)
