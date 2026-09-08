# Headless motion validation

Final verification: the default environment, with MuJoCo absent, completed
`pytest -q -rs` with **537 passed, 2 skipped**. The two skips are the optional
physics modules. All 21 frontend contract cases ran with the installed Node
dependencies. The opt-in physics environment completed both physics test files
with **25 passed**, and the scenario CLI returned exit code 0. Local API tests
were run outside the execution sandbox because its socket restrictions prevent
TestClient's cross-thread event-loop wakeup.

The optional physics harness drives the real `ArmSession` through an injected
RebotArm-shaped transport. It records the MIT setpoint, desired velocity,
gains and gravity feedforward, then advances a MuJoCo plant using the last
received command. The plant does not interpolate the application trajectory.

## Implementation notes for playback changes

The motion reference is a rest-to-rest quintic position profile. The profile
evaluates position, velocity, acceleration and jerk analytically and checks
their extrema before accepting a move. The default software limits are
`v=0.25 rad/s`, `a=0.5 rad/s²`, and `jerk=2 rad/s³`. The duration sent by the
executor is the accepted physical duration returned by the driver; it can be
longer than the requested duration when a limit requires stretching.

The executor keeps nominal timeline time separate from physical segment time.
Markers and transitions use nominal progress, while an accepted physical
duration maps the remaining segment proportion. WAIT captures one measured
pose and must continue sending the same hold reference on every control tick.
On resume, the remaining motion is replanned from that frozen reference with a
new arrival deadline.

Each move is bound to the last reference actually transmitted and carries a
generation token. A repeated stream continues that reference. Preparation has
no actuator side effects; safety preflight runs before commit, and a rejected
retarget leaves the existing motion intact. The first approach is checked by
the same profile bounds rather than only by a separate first-block estimate.

The gap scenarios retain the historical fault injection: `gap_s` is the
physics advance while the observed control tick gap is `gap_s + 0.01 s`, so
the recorded cases are `0.11 s` and `1.01 s`. Command reference jumps and
measured plant movement are reported separately. The current boundary policy
uses the existing `0.02 rad` limit tolerance, shared by the analytical profile
and the safety path checks through `backend.arm.limits`.

The normal application has no MuJoCo dependency. To install the opt-in
environment:

```bash
cd app
UV_CACHE_DIR=/tmp/rebot-uv-cache uv sync --frozen --extra physics
```

Run the deterministic validation scenarios with:

```bash
cd app
uv run --frozen --extra physics python -m backend.validation \
  --output data/validation/current.json \
  --csv data/validation/current.csv --check
```

The harness copies the RS URDF to a temporary directory because its mesh paths
are relative to the vendor layout. It injects `fusestatic=false` and verifies
that `base_link` and `gripper_end` remain named bodies. It checks the MuJoCo
static gravity vector against Pinocchio before running plant scenarios.

The baseline final-model evidence is recorded in
[`docs/evidence/motion/baseline-final-model.json`](./evidence/motion/baseline-final-model.json)
and its CSV artifact. At the recorded revision, the later transition emitted a
`2.398725 rad/s` peak command speed. A one-second gap produced a
`0.554471 rad` reference jump. A five-second WAIT pause ended with the
executor aborting on its arrival deadline. Command references and measured
plant state are separate fields in the evidence. The JSON records the git
revision, URDF hash, source hashes and runtime settings needed to reproduce
that baseline. The fixed result is recorded in
[`docs/evidence/motion/fixed-final.json`](./evidence/motion/fixed-final.json);
its CSV SHA-256 is documented in
[`docs/evidence/motion/comparison.md`](./evidence/motion/comparison.md).
The fixed run reaches the speed, gap, WAIT and retarget checks in that
comparison. These are software and optional-physics results, not real-arm
validation. Default full-suite acceptance is reported separately.

The plant uses a 1 ms timestep and MuJoCo `implicitfast` for the stiff MIT
velocity feedback. The model keeps the RS fixed finger mass and does not invent
a mapping from the gripper motor angle to finger travel. Camera payload inertia
is not calibrated by this harness.

Optional tests run only when the extra is installed:

```bash
uv run --frozen --extra physics pytest -q \
  tests/test_physics_model.py tests/test_physics_playback.py
```

The default `uv sync --frozen` path does not install MuJoCo; importing the
normal backend remains independent of the optional package. The harness is
headless and CPU-only. It validates command continuity, plant response,
kinematics and dynamics consistency, but it does not calibrate real friction,
motor firmware response, camera inertia, or the uncalibrated gripper gearing.
