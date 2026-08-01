"""Finding providers, and knowing which of them work.

Providers arrive two ways. The shutter is registered directly, because it ships
with the service. Everything else comes from an entry point::

    [project.entry-points."rebot.actions"]
    turntable = "rebot_plugin_turntable:TurntableProvider"

so installing a plugin is ``uv pip install`` plus a restart, with no host
change and no front-end change.

**A broken plugin never stops the service.** A device missing one accessory
should still lay out anchors and drive the arm; refusing to start would turn a
missing turntable into a missing machine. But it is never hidden either — a
provider that failed to import or failed its probe stays in the manifest with
the reason attached, so the edit sheet can grey it out and say why. A plugin
that quietly vanished would read as "I configured it wrong", and the operator
would go looking in the wrong place.

The runner is the single register of *which providers exist*; this module adds
discovery and health on top, and never keeps a second copy of the list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Iterable

from .base import ActionProvider
from .runner import ActionRunner

log = logging.getLogger(__name__)

#: Entry point group third-party packages advertise providers under.
ENTRY_POINT_GROUP = "rebot.actions"


@dataclass(frozen=True)
class ProviderStatus:
    id: str
    label: str
    available: bool
    #: Why not, when ``available`` is false. Shown to the operator verbatim.
    reason: str | None = None
    #: Whether the host may re-run this action after a failure.
    retryable: bool = True


class ActionRegistry:
    """Discovery and health for the providers a runner can reach."""

    def __init__(self, runner: ActionRunner) -> None:
        self._runner = runner
        self._status: dict[str, ProviderStatus] = {}
        #: Providers that could not even be constructed, so the runner has
        #: never heard of them. Kept here so they still reach the manifest.
        self._broken: dict[str, ProviderStatus] = {}

    # ── population ───────────────────────────────────────────────────────────

    def register(self, provider: ActionProvider) -> None:
        """Add a provider. Does not probe it.

        Probing has side effects — it pings a board over a serial link — and a
        side effect hidden inside wiring is one nobody can predict. Health is
        established explicitly by :meth:`probe_all` at startup, refreshed by
        ``POST /api/plugins/probe``, and filled in lazily by whoever first asks
        a question that depends on it.
        """
        self._runner.register(provider)
        self._broken.pop(provider.id, None)
        self._status.pop(provider.id, None)

    def register_all(self, providers: Iterable[ActionProvider]) -> None:
        for provider in providers:
            self.register(provider)

    def discover(self) -> None:
        """Load every installed plugin. A bad one costs itself, nothing more."""
        for entry in entry_points(group=ENTRY_POINT_GROUP):
            try:
                factory = entry.load()
                provider = factory()
            except Exception as exc:
                # Import errors, missing dependencies, a constructor that wants
                # a serial port that is not there. Loud, and survivable.
                log.error("plugin %r failed to load: %s", entry.name, exc, exc_info=True)
                self._broken[entry.name] = ProviderStatus(
                    id=entry.name,
                    label=entry.name,
                    available=False,
                    reason=f"failed to load: {exc}",
                )
                continue
            log.info("plugin %r provides action %r", entry.name, provider.id)
            self.register(provider)

    # ── health ───────────────────────────────────────────────────────────────

    def probe(self, provider_id: str) -> ProviderStatus:
        """Run one provider's self-test and record what happened."""
        provider = self._runner.provider(provider_id)
        if provider is None:
            return self._broken.get(
                provider_id,
                ProviderStatus(provider_id, provider_id, False, "not installed"),
            )

        label = getattr(provider, "label", provider_id)
        retryable = bool(getattr(provider, "retryable", True))
        try:
            provider.probe()
        except Exception as exc:
            log.warning("provider %r is not usable: %s", provider_id, exc)
            status = ProviderStatus(provider_id, label, False, str(exc), retryable)
        else:
            status = ProviderStatus(provider_id, label, True, None, retryable)
        self._status[provider_id] = status
        return status

    def probe_all(self) -> list[ProviderStatus]:
        return [self.probe(pid) for pid in self._runner.provider_ids]

    @property
    def provider_ids(self) -> list[str]:
        """Everything installed, working or not."""
        return sorted(set(self._runner.provider_ids) | set(self._broken))

    def provider(self, provider_id: str) -> ActionProvider | None:
        """The provider itself, or None. Delegates: the runner is the register."""
        return self._runner.provider(provider_id)

    def status(self, provider_id: str) -> ProviderStatus | None:
        """Last known health, without probing. None if it has never been asked."""
        return self._status.get(provider_id) or self._broken.get(provider_id)

    def ensure_status(self, provider_id: str) -> ProviderStatus:
        """Health, probing once if it is not known yet.

        Cached rather than re-probed every time: a pre-flight runs on every tap
        of an anchor, and a serial round-trip there would put the board's
        timeout in front of the operator's finger. A provider that dies between
        the probe and the action fails at the action, which is what the driver's
        exceptions and the abort-by-default policy are for.
        """
        return self.status(provider_id) or self.probe(provider_id)

    # ── the front end's view ─────────────────────────────────────────────────

    def manifest(self) -> list[dict]:
        """Everything the anchor edit sheet needs to draw a provider's form.

        Fields are *described*, not drawn: the host owns the widgets. A plugin
        that shipped its own markup would ship its own colours with it, and on
        this machine colour is a status channel rather than decoration.
        """
        out: list[dict] = []
        for provider_id in self._runner.provider_ids:
            provider = self._runner.provider(provider_id)
            if provider is None:  # pragma: no cover — unregistered mid-call
                continue
            status = self.ensure_status(provider_id)
            out.append(
                {
                    "id": provider_id,
                    "label": status.label,
                    "available": status.available,
                    "reason": status.reason,
                    "retryable": status.retryable,
                    "fields": [_field_payload(f) for f in provider.fields()],
                }
            )
        out.extend(
            {
                "id": s.id,
                "label": s.label,
                "available": False,
                "reason": s.reason,
                "retryable": s.retryable,
                "fields": [],
            }
            for s in self._broken.values()
        )
        return sorted(out, key=lambda p: p["id"])


def _field_payload(spec) -> dict:
    """Serialise a FieldSpec, dropping the keys this kind of widget ignores."""
    payload = {
        "key": spec.key,
        "kind": spec.kind,
        "label": spec.label,
        "default": spec.default,
    }
    for name in ("min", "max", "values", "when"):
        value = getattr(spec, name)
        if value is not None:
            payload[name] = value
    if spec.unit:
        payload["unit"] = spec.unit
    return payload
