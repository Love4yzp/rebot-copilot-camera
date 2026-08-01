"""Catching a bad action before the arm is standing at the anchor.

Two checkpoints, both chosen so the failure surfaces where it is cheap.

**On write.** A :class:`~backend.routines.models.PluginAction` carries an opaque
params dict; pydantic cannot check it, because only the provider knows its
shape. So the API checks it against the provider's own model as the waypoint is
stored. Skipping this would move the error to the ACTING phase — the arm at the
anchor, the subject waiting, the operator watching a routine abort for a typo
made an hour earlier.

**On play.** A routine can outlive the plugin it was written against: the
package gets uninstalled, or the board it talks to is unplugged. So the whole
routine is checked before anything moves, exactly as the kinematic pre-flight
is. Finding out by watching the arm walk to the anchor is the expensive way.
"""

from __future__ import annotations

from pydantic import ValidationError

from ..routines.models import PluginAction, Routine, Waypoint
from .registry import ActionRegistry


def validate_action_params(waypoint: Waypoint, registry: ActionRegistry) -> list[str]:
    """Reasons this waypoint's plugin actions could not be stored. Empty is fine."""
    reasons: list[str] = []
    for index, action in enumerate(waypoint.actions):
        if not isinstance(action, PluginAction):
            continue
        provider = registry.provider(action.provider)
        if provider is None:
            reasons.append(f"action {index}: no provider {action.provider!r} is installed")
            continue
        try:
            provider.params_model.model_validate(action.params)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
                for e in exc.errors(include_url=False)
            )
            reasons.append(f"action {index} ({action.provider}): {problems}")
    return reasons


def validate_providers(routine: Routine, registry: ActionRegistry) -> list[str]:
    """Reasons this routine cannot be played right now. Empty means go.

    An unavailable provider stops the run rather than being skipped. Carrying
    on would walk the whole set and deliver nothing from that action — the
    failure this project already refuses to have quietly, which is why the
    default failure policy is abort.
    """
    reasons: list[str] = []
    for wp_index, waypoint in enumerate(routine.waypoints):
        where = waypoint.note.strip() or f"waypoint {wp_index}"
        for action in waypoint.actions:
            provider_id = action.provider if isinstance(action, PluginAction) else None
            if provider_id is None:
                continue
            if registry.provider(provider_id) is None:
                reasons.append(f"{where}: no provider {provider_id!r} is installed")
                continue
            status = registry.ensure_status(provider_id)
            if not status.available:
                reasons.append(f"{where}: {provider_id} is unavailable ({status.reason})")
        reasons.extend(f"{where}: {r}" for r in validate_action_params(waypoint, registry))
    return reasons
