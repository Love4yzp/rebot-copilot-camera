"""Catching a bad marker before the arm is standing at the pose.

Two checkpoints, both chosen so the failure surfaces where it is cheap.

**On write.** A marker carries an opaque params dict; pydantic cannot check
it, because only the provider knows its shape. So the API checks it against
the provider's own model as the blocks are stored. Skipping this would move
the error to mid-run — the arm at the pose, the subject waiting, the operator
watching a sequence abort for a typo made an hour earlier.

**On execute.** A sequence can outlive the plugin it was written against: the
package gets uninstalled, or the board it talks to is unplugged. So the whole
block list is checked before anything moves, exactly as the kinematic
pre-flight is. Finding out by watching the arm walk to the pose is the
expensive way.
"""

from __future__ import annotations

from pydantic import ValidationError

from ..sequences.models import WAIT_KIND, Block, EventMarker
from .registry import ActionRegistry


def _provider_markers(blocks: list[Block]) -> list[tuple[int, EventMarker]]:
    """Every marker that names a provider, with its block index. The built-in
    wait marker has no provider and nothing to validate."""
    return [
        (index, marker)
        for index, block in enumerate(blocks)
        for marker in block.markers
        if marker.kind != WAIT_KIND
    ]


def validate_marker_params(blocks: list[Block], registry: ActionRegistry) -> list[str]:
    """Reasons these blocks' markers could not be stored. Empty is fine."""
    reasons: list[str] = []
    for block_index, marker in _provider_markers(blocks):
        where = f"block {block_index}, marker {marker.kind}"
        provider = registry.provider(marker.kind)
        if provider is None:
            reasons.append(f"{where}: no provider {marker.kind!r} is installed")
            continue
        try:
            provider.params_model.model_validate(marker.params)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
                for e in exc.errors(include_url=False)
            )
            reasons.append(f"{where}: {problems}")
    return reasons


def validate_providers(blocks: list[Block], registry: ActionRegistry) -> list[str]:
    """Reasons this sequence cannot be executed right now. Empty means go.

    An unavailable provider stops the run rather than being skipped. Carrying
    on would walk the whole set and deliver nothing from that marker — the
    failure this project already refuses to have quietly, which is why a
    failed marker aborts the run.
    """
    reasons: list[str] = []
    for block_index, marker in _provider_markers(blocks):
        where = f"block {block_index}, marker {marker.kind}"
        if registry.provider(marker.kind) is None:
            reasons.append(f"{where}: no provider {marker.kind!r} is installed")
            continue
        status = registry.ensure_status(marker.kind)
        if not status.available:
            reasons.append(f"{where}: {marker.kind} is unavailable ({status.reason})")
    return reasons
