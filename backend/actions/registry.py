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

**Nothing a plugin does may cost another plugin, or the service.** The host
assumes third-party code is malformed, slow and crash-prone, and every place
this module touches one is written that way: the shape is checked before the
object is registered at all, ``fields()`` is called defensively because the
manifest carries every provider and one raising exception must not empty the
list, and ``probe()`` runs on the provider's worker thread under a deadline
because a self-test that hangs would otherwise take the request thread with it.

The runner is the single register of *which providers exist*; this module adds
discovery and health on top, and never keeps a second copy of the list.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Iterable

from pydantic import BaseModel

from .base import ActionProvider, ActionTimeout, ProviderBusy
from .runner import ActionRunner

log = logging.getLogger(__name__)

#: Entry point group third-party packages advertise providers under.
ENTRY_POINT_GROUP = "rebot.actions"

#: How long a self-test may take before the host stops waiting for it. Generous
#: for a serial ping, short enough that a wedged accessory does not hold up the
#: plugin list or the pre-flight in front of an operator's finger.
PROBE_TIMEOUT_S = 5.0

#: What an id may look like. It is stored in routine JSON and addressed over
#: HTTP, so it is kept to the characters that survive both without quoting.
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def check_shape(provider: object) -> None:
    """Raise ``ValueError`` unless this object can be used as a provider.

    Called before the runner ever hears of it, and the message is written for
    the plugin author, because they are the only one who can fix it. Attributes
    are inspected, never called: running third-party code to decide whether to
    accept third-party code is how a misspelled attribute becomes a service that
    will not start.

    ``label`` and ``retryable`` are absent from this list on purpose — both have
    a safe default, and refusing to load an otherwise working accessory over a
    missing display name would be the host being precious about cosmetics.
    """
    provider_id = getattr(provider, "id", None)
    if not isinstance(provider_id, str) or not _ID.match(provider_id):
        raise ValueError(
            f"id must be a string matching {_ID.pattern}, got {provider_id!r}"
        )

    params_model = getattr(provider, "params_model", None)
    if not (isinstance(params_model, type) and issubclass(params_model, BaseModel)):
        raise ValueError(f"{provider_id}: params_model must be a pydantic BaseModel subclass")

    for name in ("fields", "probe", "run"):
        if not callable(getattr(provider, name, None)):
            raise ValueError(f"{provider_id}: {name}() is missing")


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

    def register(self, provider: ActionProvider, *, replace: bool = False) -> None:
        """Add a provider. Checks its shape; does not probe it.

        Probing has side effects — it pings a board over a serial link — and a
        side effect hidden inside wiring is one nobody can predict. Health is
        established explicitly by :meth:`probe_all` at startup, refreshed by
        ``POST /api/plugins/probe``, and filled in lazily by whoever first asks
        a question that depends on it.

        **An id already in use is refused.** Ids are how stored actions name
        their provider, and the executor dispatches every ``ShutterAction`` to
        the literal id ``shutter``; a plugin that declared that id would quietly
        become the camera, and a whole set would come back empty with nothing
        having raised. ``replace`` is for the host swapping its own built-in —
        the shutter provider is rebuilt when the real driver is chosen at
        startup — and is not something discovery can ask for.
        """
        check_shape(provider)
        if not replace and self._runner.provider(provider.id) is not None:
            raise ValueError(
                f"id {provider.id!r} is already registered; "
                "ids name a provider in stored routines, so two cannot share one"
            )
        self._runner.register(provider)
        self._broken.pop(provider.id, None)
        self._status.pop(provider.id, None)

    def register_all(self, providers: Iterable[ActionProvider]) -> None:
        for provider in providers:
            self.register(provider)

    def discover(self) -> None:
        """Load every installed plugin. A bad one costs itself, nothing more.

        Every step is inside the guard, including reading ``provider.id``: an
        attribute that is missing or misspelled is exactly what a broken plugin
        looks like, and letting that escape would make one bad package the
        reason the whole machine will not start.
        """
        for entry in entry_points(group=ENTRY_POINT_GROUP):
            try:
                provider = entry.load()()
            except Exception as exc:
                # Import errors, missing dependencies, a constructor that wants
                # a serial port that is not there. Loud, and survivable.
                log.error("plugin %r failed to load: %s", entry.name, exc, exc_info=True)
                self._record_broken(entry.name, f"failed to load: {exc}")
                continue

            try:
                self.register(provider)
            except Exception as exc:
                # A malformed provider, or one claiming an id that is taken.
                log.error("plugin %r was refused: %s", entry.name, exc, exc_info=True)
                self._record_broken(entry.name, str(exc))
                continue

            log.info("plugin %r provides action %r", entry.name, provider.id)

    def _record_broken(self, name: str, reason: str) -> None:
        """List a plugin that never became a provider, under its package's name.

        Keyed by entry point name rather than by the id it wanted, because a
        plugin that failed this early has no id the host is willing to trust —
        and the entry point name is what ``uv pip list`` shows the operator.
        """
        self._broken[name] = ProviderStatus(
            id=name, label=name, available=False, reason=reason
        )

    # ── health ───────────────────────────────────────────────────────────────

    def probe(self, provider_id: str) -> ProviderStatus:
        """Run one provider's self-test and record what happened.

        The self-test runs on the provider's own worker thread with a deadline,
        like an action does. It used to be called straight from here, which put
        third-party code on whichever thread asked the question: a provider that
        hung in ``probe`` wedged ``GET /api/plugins``, the plugin refresh and the
        pre-flight that runs before the arm moves — the exact failure the runner
        exists to keep off the control loop, one layer up.
        """
        provider = self._runner.provider(provider_id)
        if provider is None:
            return self._broken.get(
                provider_id,
                ProviderStatus(provider_id, provider_id, False, "not installed"),
            )
        return self._settle(provider_id, self._runner.submit_probe(provider_id, PROBE_TIMEOUT_S))

    def probe_all(self) -> list[ProviderStatus]:
        """Self-test everything, side by side.

        Submitted first and collected after, so ten accessories take one
        timeout between them rather than ten in a row. Each is on its own
        worker thread already; waiting on them one at a time would be the host
        adding the queue back.
        """
        ids = self._runner.provider_ids
        jobs = [self._runner.submit_probe(pid, PROBE_TIMEOUT_S) for pid in ids]
        return [self._settle(pid, job) for pid, job in zip(ids, jobs)]

    def _settle(self, provider_id: str, job) -> ProviderStatus:
        """Wait out one submitted self-test and record the verdict."""
        provider = self._runner.provider(provider_id)
        label = getattr(provider, "label", provider_id) if provider is not None else provider_id
        retryable = bool(getattr(provider, "retryable", True)) if provider is not None else True

        if job.wait(PROBE_TIMEOUT_S):
            error = job.error
        else:
            # The worker has not come back. Whether the job's own clock agrees
            # is beside the point here: this caller waited real seconds and got
            # no answer, so it says so rather than reading an unresolved job as
            # a pass. Reporting an accessory as healthy because nothing said
            # otherwise is the shape of wrong answer this project keeps out.
            job.abandon()
            error = job.error or ActionTimeout(
                f"{provider_id} did not answer its self-test within {PROBE_TIMEOUT_S:g}s"
            )

        if isinstance(error, ProviderBusy):
            # Mid-action. A provider that accepted work is reachable, so the
            # last verdict stands rather than being overwritten with a failure
            # that is really about timing.
            log.debug("skipped the self-test for %r: it is busy", provider_id)
            return self._status.get(provider_id) or ProviderStatus(
                provider_id, label, True, None, retryable
            )

        if error is not None:
            log.warning("provider %r is not usable: %s", provider_id, error)
            status = ProviderStatus(provider_id, label, False, str(error), retryable)
        else:
            status = ProviderStatus(provider_id, label, True, None, retryable)
        self._status[provider_id] = status
        return status

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
            fields, form_error = _fields_of(provider)
            out.append(
                {
                    "id": provider_id,
                    "label": status.label,
                    "installed": True,
                    "available": status.available and form_error is None,
                    "reason": form_error or status.reason,
                    "retryable": status.retryable,
                    "fields": fields,
                }
            )
        out.extend(
            {
                "id": s.id,
                "label": s.label,
                #: Never became a provider, so nothing can be configured against
                #: it: the edit sheet offers it nowhere and the write-time check
                #: would refuse it anyway. Listed all the same — a plugin that
                #: disappeared reads to an operator as their own mistake.
                "installed": False,
                "available": False,
                "reason": s.reason,
                "retryable": s.retryable,
                "fields": [],
            }
            for s in self._broken.values()
        )
        return sorted(out, key=lambda p: p["id"])


def _fields_of(provider: ActionProvider) -> tuple[list[dict], str | None]:
    """One provider's form, or the reason there isn't one.

    ``fields()`` is third-party code called while building a list that carries
    every provider, so it is guarded: an exception escaping here would answer
    ``GET /api/plugins`` with a 500 and take every other accessory off the edit
    sheet with it. One broken plugin costs itself and nothing else.
    """
    try:
        return [_field_payload(f) for f in provider.fields()], None
    except Exception as exc:
        log.error("provider %r could not describe its form: %s", provider.id, exc, exc_info=True)
        return [], f"its controls could not be built: {exc}"


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
