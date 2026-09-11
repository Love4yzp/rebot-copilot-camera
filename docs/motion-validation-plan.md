# Playback control and optional physics validation

> Historical plan for offline motion validation. The current runtime architecture supersedes its lightweight-sim/default-dependency constraints; see [ARCHITECTURE](ARCHITECTURE.md). Preserve the evidence and hardware restrictions.

## Objective

Reproduce playback discontinuities through the production command path without
hardware, fix confirmed defects, and retain executable regression evidence.
MuJoCo is an opt-in validation dependency. Existing application installation,
lightweight simulation, API contracts, and default test collection must work
without it. No real hardware or backend server is started by this work.

## Baseline

- Application revision: `176efb12d633f95bbabf982d13fd129d8c589061`.
- Arm submodule: `d54040596faa94bdc4f8ad93f3f06b33dfe3a1bf`.
- `app/backend/arm/session.py:178`: absolute-time cubic position profile;
  desired velocity is zero and per-joint gains are overridden at line 320.
- `app/backend/core/executor.py:471`: speed constraint only covers block zero.
- `app/backend/core/executor.py:375`: resume changes block time but not motion
  time or the arrival deadline.
- `app/backend/safety/watchdog.py:74`: isolated long gaps escape the sustained
  lateness rule.
- `app/backend/arm/sim.py:244`: lightweight plant advances its own trajectory;
  this is not a valid model of a motor holding the last received MIT command.

## Ownership and delivery order

1. Environment probe: a luna worker prepares dependencies and imports the RS
   model using temporary files; it does not edit tracked application files.
2. Physics harness: a luna worker owns optional dependency metadata, validation
   modules/tests, and validation documentation. Capture baseline evidence before
   changing the production control implementation.
3. Playback fix: a luna worker owns session/profile, simulator parity, executor,
   watchdog/controller integration and their regression tests. Starts after the
   harness has recorded baseline results.
4. Independent review: a fresh read-only agent reviews the final diff against
   this specification. The primary agent reruns key acceptance commands and
   inspects evidence, resolving disagreements before accepting the change.

Do not change vendor sources/submodule revision, device scripts, deployment,
firmware, frontend appearance, public pose/sequence schemas, or live hardware
configuration. Preserve existing documentation sections and update both READMEs
for any new user commands. No commits or remote publishing during implementation.

## Optional physics harness

Proposed paths (workers may choose an equally small layout with explanation):

- `app/pyproject.toml:1` and `app/uv.lock`: optional extra `physics`, containing
  MuJoCo only as needed. Do not add it to ordinary dependencies or default dev.
- `app/backend/validation/`: opt-in headless CLI, model adapter, motor plant,
  deterministic scenarios and JSON/CSV evidence output. Importing the normal app
  must not import MuJoCo. No new repository, service or plugin abstraction.
- `app/tests/test_physics_*.py`: optional tests skip only for absent MuJoCo;
  an installed but broken model/runtime must fail, not silently skip.
- `app/backend/arm/session.py:53`: if necessary, add a narrow optional injected
  arm transport constructor argument. The default still constructs the upstream
  RebotArm. Injection must bypass all physical connection/import side effects.
- `docs/motion-validation.md`, README.md, README.zh-CN.md: installation, commands,
  coverage, limitations, baseline/fixed comparisons and reproducibility details.

The harness must instantiate the real ArmSession. Gap, WAIT and retarget
acceptance must also use the real Controller and SequenceExecutor. Supply an
injected RebotArm-shaped transport exposing joint
names, groups, get_state/connect and MIT sends. It records `q_des`, `v_des`, kp,
kd, and feedforward torque per motor. At each physics substep apply
`clip(kp*(q_des-q) + kd*(v_des-v) + tau_ff, -tau_limit, tau_limit)`.
The plant must retain the last command until the next send; it must never
interpolate the application's motion on its own. Fault scheduling and the
physics clock are separate from application ticks. No time.sleep in tests.

Import the existing RS URDF using a temporary derived model, resolving mesh
paths and preserving joint axes, inertial frames and fixed base. Start with six
actuated arm joints and fixed finger geometry, preserving gripper mass for the
gripper payload. A motor-channel mock may expose gripper feedback separately;
do not invent the uncalibrated finger-to-motor gearing. Clearly label this
limitation. Verify named q/v mapping, FK at several legal configurations and
static gravity against Pinocchio before using the plant to assess the fixes.
Exclude structural adjacent contacts explicitly; do not disable all collisions
or infer validity from an arbitrary nonzero posture. Do not silently replace
invalid inertias. Camera payload dynamics requires explicit inertia assumptions;
the current gravity-only payload model is not a calibrated dynamic camera model.

Initial physics timestep: 0.001 s, application commands: 0.01 s. Run a smaller
step comparison for convergence. These are validation settings, not changes to
the real controller frequency. A visual viewer is optional; headless execution
and machine-readable evidence are mandatory.

## Required reproductions before production changes

Use identical scene/initial state/seed/settings for baseline and fixed runs.
Preserve baseline JSON/CSV plus revision metadata outside temporary caches.
Measure commands and actual plant response separately; never describe a command
jump as a measured physical jump.

1. A later sequence transition with insufficient duration: show its peak command
   speed exceeds the approach limit (use a collision-free physical path).
2. Cubic start/finish acceleration discontinuity and zero desired velocity:
   record emitted MIT command derivatives and tracking error.
3. Omit control ticks for 0.1 and 1 s while the plant continues stepping:
   show stale command persistence and the recovery target jump/abort behavior.
4. Place WAIT inside a transition, pause for 0.1, 1 and 5 s, then resume:
   show timebase inconsistency, including deadline expiry where applicable.
5. Partial joint target while an unspecified feedback channel varies:
   demonstrate profile restart or loss of progress.
6. Retarget an in-progress move: quantify the reference discontinuity.

If a suspected physical symptom cannot be reproduced, report that outcome and
the tested assumptions. A command-layer reproduction remains valid evidence for
a software defect, but is not proof of a particular real-world oscillation.

## Production fix design

Keep FK, gravity and collision calculations in the existing upstream/Pinocchio
paths. The local MIT motion-profile exception already permits a small shared
profile helper used by ArmSession and lightweight SimArm; no custom IK or
general trajectory framework.

- Replace cubic with a quintic rest-to-rest profile. Return position and
  analytical velocity/acceleration. For normal stationary moves its normalized
  peak speed is 1.875, peak acceleration is 10/sqrt(3), peak jerk is 60.
  Compute duration from displacement and configured v/a/j ceilings for every
  move, not just the first. Preserve requested duration as a lower bound.
  Initial software limits: 0.25 rad/s, 0.5 rad/s^2, 2 rad/s^3. These are
  conservative validation defaults, not hardware calibration.
- Keep motion limits in one backend configuration source with finite positive
  validation; avoid changing the tuning API/frontend contract just to expose
  additional knobs. Existing approach speed must still constrain first/goto
  moves. `ArmDriver.move_to` returns the accepted physical duration in seconds;
  repeated streaming returns the same duration without restarting. Both drivers
  implement this. Executor sets its deadline after accepting the first command,
  using the returned duration; no private driver introspection.
- Retarget from the current reference q/v/a using a quintic boundary-value
  segment ending at target with zero v/a. Check polynomial extrema including
  intermediate joint positions, speed, acceleration and jerk. Increase duration
  only within a bounded search; reject infeasible requests without destroying
  the active valid motion. A narrow conservative rejection is acceptable;
  complete feasibility or minimum-time search is not required. Never assert
  that longer duration always removes overshoot. Do not use sparse time samples
  as proof of polynomial bounds. Candidate curved retarget paths must also pass
  the existing controller safety preflight (document its sampling limits).
  If that requires a wider prepare/commit protocol, report before implementing.
  Use a narrow prepare/commit protocol on ArmDriver: `prepare_move(target,
  requested_duration) -> PreparedMotion`, `commit_move(prepared) -> float`.
  Preparation has no actuator side effects; the immutable plan carries the
  accepted duration and deterministic path samples for Controller preflight.
  Driver checks exact polynomial joint/derivative bounds; Controller checks
  collision samples through its existing safety entry point before commit.
  Prepared motion carries a generation token and cannot overwrite a different
  subsequently committed/held motion. The normal move_to streaming method
  remains the executor's interface and returns accepted duration. For retarget,
  Controller prepares, validates, commits and starts the replacement while
  holding its existing lock; only then aborts the old executor. Ensure the new
  executor's first command continues the committed profile rather than planning
  again. Rejected retarget leaves old execution and its reference intact; tests
  must exercise a near-limit reversal and a collision preflight rejection.
  A committed profile starts from the last reference actually transmitted by
  the driver. Each subsequent transmission advances the driver's motion
  revision, so a stale prepared profile cannot replace a newer send or hold.
  Position-bound checks use the shared `0.02 rad` tolerance; keep this value
  consistent across the profile and safety paths.
  Sampling is only the existing conservative collision check, never the proof
  of q/v/a/jerk bounds. Do not add continuous-collision or generic planner APIs.
- Freeze unspecified target joints at command acceptance. Compare the original
  requested targets for continuation; feedback noise must not create a command.
- Send the reference velocity. Compute gravity from measured posture. Capture
  those effects separately. Keep deployed hold gains at 50/3 for this iteration;
  document that hardware YAML MIT gains are not yet consumed and correct the
  misleading comment. Enabling 150/10 is not a prerequisite for passing physics
  validation and requires later calibration. Gain configuration is deferred.
- Maintain MIT sends during holds, settling and WAIT. A WAIT is a deliberate
  hold interruption: capture measured pose once and send hold in that tick,
  then stream that fixed hold on every WAIT tick. Replan remaining motion from
  this stationary reference on resume with a fresh deadline. Preserve marker
  ordering and never refire a completed marker. Pause time must not advance the
  physical trajectory. Transition marker proportions follow normalized physical
  segment progress. Keep public `t_in_block` in nominal seconds: for each active
  segment, nominal time advances by remaining_nominal/accepted_physical_duration.
  On resume begin at the frozen nominal progress and remap the remaining
  proportion. Hold timing stays wall/simulation seconds. Once segment progress
  reaches its endpoint, delayed actions may keep the block open but not restart
  motion. Cover WAIT at 0/0.5/1, two waits, same-time marker order, delayed action
  completion and no duplicate events. A WAIT delayed by an in-flight action
  freezes actual current progress rather than rewinding to its scheduled time.
- Treat a single excessive control gap separately from sustained lateness.
  Initial absolute threshold: greater than 0.1 s, independently configurable
  from sustained lateness. Test both sides with numerical timing tolerance.
  Engage the existing holding latch before allowing the executor to advance on
  recovery; no automatic resume. Preserve immediate torque-holding estop and
  never call disable_all/estop on the upstream arm.

The implementation worker must report any conflict between these choices and
existing contracts before making a broader change. Teaching threshold redesign,
camera calibration, Cartesian planning and continuous collision detection are
outside this playback-fix scope.

## Acceptance

- Default install/import/lightweight tests succeed without MuJoCo. Explicit
  physics invocation without the extra gives an actionable error.
- Fixed scenarios demonstrate bounded analytic reference speed/acceleration/jerk,
  bounded adjacent sampled command differences, continuous
  normal q/v/a transitions, no partial-target restart, explicit safe gap handling,
  and successful resumed motion after short/long waits. Safety/WAIT hard holds
  are reported as intentional interruptions, not silently excluded metrics.
- Physical runs include finite state, tracking error, final arrival, torque
  saturation, limit/contact events, and wall-time/RSS measurements. Do not pass
  merely because the animation finishes or assert universally improved tracking.
- Model consistency initial tolerances: FK translation/rotation 1e-6 m/rad,
  static gravity 1e-5 N m. Step convergence: max joint position difference
  0.002 rad for 1 ms versus 0.5 ms on the same smooth non-contact scenario.
  Environment probe must report if these are inappropriate before fixed runs;
  do not relax thresholds after seeing a failed fix. Do not call zero-order-held
  digital commands mathematically continuous, or quintic jerk continuous.
  Payload/friction variations are sensitivity tests, not calibration.
- New core regressions fail against baseline behavior and pass after the fixes.
  Optional physics tests run with the extra. Run the existing backend suite,
  architecture/safety checks and relevant contracts; explain skips/failures.
- Evidence includes exact commands, revisions, dependency versions, seeds,
  configuration, before/after metrics, test outputs and limitations.
