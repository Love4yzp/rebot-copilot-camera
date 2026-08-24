# Teach & Repeat

A teach-and-repeat platform: named poses, sequences of holds and transitions, and an arm that goes and holds. This glossary is the ubiquitous language. Implementation lives in `docs/ARCHITECTURE.md`.

## Language

**Activity**:
The one thing the arm is doing: idle, teaching, playing, resting, or safelock. Exclusive. _Avoid_: mode soup, overlapping flags, “status”.

**Latch**:
A cross-cutting freeze that outranks Activity. Engaged, the arm holds torque in place and every motion intent is refused. Not an Activity. _Avoid_: estop-as-mode.

**Hold**:
Torque on, pose pinned. Idle after a move is a Hold. Emergency stop is a Hold. _Avoid_: disable, drop, power cut.

**Rest**:
Zero torque at the mechanical stops. Not idle: idle holds, rest does not. _Avoid_: idle, park, sleep.

**Goto**:
Set the arm’s destination to one library pose and hold there. An ephemeral play; not stored. A second Goto while playing retargets. _Avoid_: treating Goto as a second motion system beside Play.

**Play**:
Walk a stored sequence. Exclusive: a second Play is refused until Stop. _Avoid_: “playback” as a user word (界面用「执行」).

**Contact**:
External-torque residual past a dwell window. Geometric self-collision on the URDF is a different thing. _Avoid_: collision (reserved for preflight self-collision).

**SafeLock**:
A recoverable lock from Contact, disconnect, or a future envelope breach. Holds. Does not auto-resume. Does not auto-enter teaching. _Avoid_: mixing with Latch / Estop.

**Estop**:
The human or watchdog engaging the Latch. _Avoid_: SafeLock, Rest.

**Pose**:
A named joint configuration in the library. Hold blocks link it by id. _Avoid_: waypoint, anchor, 机位.

**Sequence**:
Holds and transitions on a timeline, with markers pinned inside blocks. _Avoid_: routine, playlist, program.

**Intent**:
A command named against Activity (teach on/off, rest on/off, play, goto, stop, resume-wait, finish, fault, unlock). HTTP is an adapter over Intent. _Avoid_: each endpoint inventing its own 409 logic.
