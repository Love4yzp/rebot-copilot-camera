"""A plugin author's development loop, with no arm and no camera in it.

::

    uv run -m backend.actions.check                       # list what is installed
    uv run -m backend.actions.check turntable             # show its manifest
    uv run -m backend.actions.check turntable --probe     # run its self-test
    uv run -m backend.actions.check turntable --run '{"degrees": 90}'

The last one is the useful one: it validates the params against the provider's
own model, builds a real :class:`~backend.actions.base.ActionContext`, and
submits through the real :class:`~backend.actions.runner.ThreadedRunner` with a
real timeout. So a provider that blocks forever, or raises something unexpected,
or ignores its params, fails here rather than at an anchor with a subject
waiting.

The same idea as SimArm and SimShutter: the no-hardware loop is infrastructure,
not a convenience. A plugin author's first day should not need a 48 V arm.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .base import ActionContext
from .registry import ActionRegistry
from .runner import ThreadedRunner

#: Generous. This is a person at a terminal watching one action, not a routine.
DEFAULT_TIMEOUT_S = 30.0


def _registry() -> ActionRegistry:
    """Everything the service would have, including the built-in shutter.

    The shutter driver is built unopened, so listing and inspecting work with
    no board attached; ``--probe`` is what actually reaches for one.
    """
    from ..shutter import create_shutter
    from .shutter import ShutterProvider

    registry = ActionRegistry(ThreadedRunner())
    driver, _ = create_shutter()
    registry.register(ShutterProvider(driver))
    registry.discover()
    return registry


def _context() -> ActionContext:
    """A plausible context. The joints are a pose, not a live arm — a provider
    that needed more than this would be reaching for something it cannot have."""
    return ActionContext(
        routine_id="check",
        routine_name="backend.actions.check",
        waypoint_index=0,
        waypoint_note="check",
        joints={},
        emit=lambda name, data: print(f"  event  {name}  {json.dumps(data, ensure_ascii=False)}"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.actions.check")
    parser.add_argument("provider", nargs="?", help="provider id; omit to list them all")
    parser.add_argument("--probe", action="store_true", help="run the provider's self-test")
    parser.add_argument("--run", metavar="JSON", help="run the action once with these params")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    registry = _registry()

    if args.provider is None:
        manifest = registry.manifest()
        if not manifest:
            print("no providers installed")
            return 0
        for entry in manifest:
            mark = "ok " if entry["available"] else "DOWN"
            print(f"{mark}  {entry['id']:<20} {entry['label']}")
            if entry["reason"]:
                print(f"      {entry['reason']}")
        return 0

    provider = registry.provider(args.provider)
    if provider is None:
        print(f"no provider {args.provider!r} is installed", file=sys.stderr)
        print(f"installed: {', '.join(registry.provider_ids) or '(none)'}", file=sys.stderr)
        return 2

    if args.probe:
        status = registry.probe(args.provider)
        print(f"probe: {'ok' if status.available else 'FAILED — ' + str(status.reason)}")
        if not status.available:
            return 1

    if args.run is not None:
        try:
            params = provider.params_model.model_validate(json.loads(args.run))
        except Exception as exc:
            print(f"params rejected by {args.provider}: {exc}", file=sys.stderr)
            return 2

        runner = ThreadedRunner([provider])
        print(f"running {args.provider} with {params!r}")
        job = runner.submit(args.provider, params, _context(), args.timeout)
        job.wait(args.timeout)
        runner.close()
        if job.error is not None:
            print(f"FAILED: {type(job.error).__name__}: {job.error}", file=sys.stderr)
            return 1
        print("ok")
        return 0

    print(json.dumps(next(e for e in registry.manifest() if e["id"] == args.provider),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
