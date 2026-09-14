# ADR 0002: Robot library boundary

## Status

Accepted.

## Context

The safety layer directly owned Pinocchio models, geometry data and `SE3`
values, while the real-arm driver directly imported upstream actuator and
dynamics modules. Those imports made the intended dependency boundary prose
rather than a machine-verifiable fact. The dangerous failure remains silent:
upstream's default asset lookup selects the DM robot, not the deployed RS arm.

The pinned upstream public API was inspected rather than inferred from package
names:

| Capability | Upstream public API | Decision |
|---|---|---|
| Explicit model loading | `kinematics.robot_model.load_robot_model(urdf_path)` | Use with the absolute RS path from `backend.assets` |
| Forward kinematics | `kinematics.forward_kinematics.compute_fk(model, q, frame_name)` | Use directly; copy arrays into a project `RobotPose` |
| Inverse kinematics | `kinematics.inverse_kinematics.solve_ik` / `compute_ik` | Available upstream; not needed by the current safety facade |
| Gravity | `dynamics.robot_model.load_dynamics_model` plus `compute_generalized_gravity` | Use through the runtime adapter with the explicit effective URDF |
| Joint / Cartesian trajectories | `trajectory.plan_joint_space_trajectory`, `plan_cartesian_geodesic_trajectory`, `track_trajectory` | Available upstream; current MIT smoothstep exception remains unchanged |
| Actuator | `actuator.rebotarm.RebotArm` and grouped MIT commands | Use only through the runtime adapter; MotorBridge remains upstream-owned |
| Self-collision and path collision | No public collision model, pair filtering, or query API | Implement only in the lowest `rebot/model.py` adapter with Pinocchio |

## Decision

Define a project-owned `RobotModel` port in `backend.arm.robot_model`. Safety
keeps its existing `ArmModel`, `validate_pose` and `validate_sequence` surface
as a compatibility facade, but delegates every operation to that port. The
port exposes only project records and NumPy arrays; no Pinocchio object crosses
it.

Construct `RebotRobotModel` with an absolute URDF, package directory, end frame
and ordered arm-joint names supplied by `backend.assets`. It must first build
the rigid-body model through upstream `load_robot_model(urdf_path=...)`.
Because upstream has no collision API, this adapter alone may use Pinocchio to
load collision geometry, remove the eight rest-contact structural pairs, and
run the unchanged 12-sample straight-joint-space path check.

Put direct `RebotArm` and dynamics imports in `integrations/rebot/runtime.py`.
`ArmSession` continues to own the single real actuator instance and all safe
MIT semantics, but depends only on adapter functions. Project code never
imports MotorBridge.

Lock the decision with an AST test whose allowlist is exact:

- `pinocchio`: `integrations/rebot/model.py` only;
- `reBotArm_control_py`: `integrations/rebot/model.py` and `runtime.py` only;
- `motorbridge`: no project imports.

## Consequences

Controller and safety code cannot receive Pinocchio types. Asset selection,
collision-pair calibration, path sampling, caching, error strings, gravity
mapping, actuator ownership and emergency-stop hold behavior remain unchanged.
The adapter is intentionally coupled to the upstream model representation for
the missing collision operations; an upstream collision API can later remove
that sole exception and the exact allowlist will then require cleanup.
