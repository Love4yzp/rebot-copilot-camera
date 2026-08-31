# Activity is exclusive; the latch stays cross-cutting

The controller used overlapping flags (`_teaching`, `_resting`, `_executor`) and derived `mode` by priority. Every new behaviour (rest, disconnect lock, contact, retarget) would add another flag and force every caller to re-audit. That is how this codebase started needing an AI rewrite each session.

**Activity** is a closed exclusive set. **Intent** is the only way to change it. The table `decide(activity, intent) -> Decision` is the interface: adding SafeLock or Goto-retarget is a new row, not a new flag. Effects name what the control loop must do to the arm; the table does not touch hardware.

The **latch is not an Activity**. A freeze that some activities might forget to enter is how a 48 V arm moves under a stop. Callers still check the latch first; `mode == "estop"` is a view, not a table state.

HTTP stays resource-shaped (`/api/poses/{id}/goto`, `/api/teach`, …). Handlers parse and call `Controller.intend`. We do not add a second command bus.

Goto and Play are different intents: a second Goto retargets; a second Play is refused. That is the motion model (set destination vs run this tape), not a UI preference.
