"""Running provider work without stalling the control loop.

The control loop is what holds the arm up. Nothing a provider does may happen
on it — and providers block by nature: ``Esp32Shutter.shoot`` waits up to six
seconds for a camera waking over BLE, ``pair`` up to thirty.

That is not hypothetical. Before this module existed the executor called the
shutter driver directly from ``Controller.tick``, so a burst against a slow
camera stalled the loop for seconds at a time. Two of those back to back cross
the watchdog's half-second late-tick grace and engage the emergency stop: a
camera that was merely slow looked exactly like an arm that had been lost, and
the shoot ended.

So the executor submits a :class:`Job` and polls it, one poll per tick, and the
loop keeps ticking at full rate the whole time.

**One worker thread per provider, one job at a time on each.** Per provider,
because a wedged shutter must not take a gripper down with it. One at a time,
because two jobs on one end effector is two things driving the same hardware,
and ``Esp32Shutter``'s protocol is built around a single request in flight.

**Timeouts are judged by the reader, not the worker.** Python cannot kill a
thread, so a provider that never returns never returns. The job gives up at its
deadline and reports :class:`~backend.actions.base.ActionTimeout`; the orphaned
thread runs on, and its provider stays out of service until it comes back
rather than having the next anchor queue up behind a corpse.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Iterable, Protocol

from pydantic import BaseModel

from .base import ActionContext, ActionError, ActionProvider, ActionTimeout, ActionUnavailable

log = logging.getLogger(__name__)

#: How long to wait for worker threads at shutdown. Short: a hung provider is
#: exactly the case here, and the process is on its way out anyway.
SHUTDOWN_JOIN_S = 1.0


class Job:
    """Handle on one submitted action.

    Read from the control loop, written by a worker thread. Resolution happens
    once and cannot be taken back: whoever gets there first — the worker
    finishing, or the reader hitting the deadline — decides the outcome.
    """

    def __init__(self, provider_id: str, deadline: float, clock: Callable[[], float]) -> None:
        self.provider_id = provider_id
        self._deadline = deadline
        self._clock = clock
        self._lock = threading.Lock()
        self._resolved = False
        self._error: BaseException | None = None
        self._abandoned = False
        #: Set when the *worker* finishes. Not set by a deadline expiring: the
        #: point of waiting is to wait for the provider, and a timeout is
        #: precisely the case where it has not arrived.
        self._settled = threading.Event()

    # ── reader side (control loop) ───────────────────────────────────────────

    @property
    def done(self) -> bool:
        """Whether the outcome is known. Never goes back to False."""
        with self._lock:
            return self._settle()

    @property
    def error(self) -> BaseException | None:
        """What went wrong, or None if it worked or is still running.

        Settles the deadline first, exactly as :attr:`done` does. Reading only
        this on a job that had timed out would otherwise answer ``None``, which
        reads as success — a silently wrong answer about whether the camera
        fired, which is the kind this project goes out of its way not to have.
        """
        with self._lock:
            self._settle()
            return self._error

    def _settle(self) -> bool:
        """Resolve on the deadline if the worker has not got there first.

        Caller holds the lock. Returns whether the outcome is known.
        """
        if self._resolved:
            return True
        if self._clock() < self._deadline:
            return False
        self._resolved = True
        self._error = ActionTimeout(
            f"{self.provider_id} did not return within its timeout; it may still be running"
        )
        log.warning("action %s timed out; its worker thread is still busy", self.provider_id)
        return True

    def wait(self, timeout_s: float) -> bool:
        """Block until the worker resolves this job. Tests and tooling only.

        The control loop never calls this — waiting is the thing this module
        exists to avoid. It is here so a test can synchronise on a worker
        without sleeping, and so ``backend.actions.check`` can run one action
        from a command line.
        """
        return self._settled.wait(timeout_s)

    def abandon(self) -> None:
        """Stop caring about the outcome. Called when the routine aborts.

        The provider's work is not cancelled — a serial write already on its way
        cannot be recalled — but nothing will act on how it turns out.
        """
        with self._lock:
            self._abandoned = True

    @property
    def abandoned(self) -> bool:
        with self._lock:
            return self._abandoned

    # ── worker side ──────────────────────────────────────────────────────────

    def _resolve(self, error: BaseException | None) -> bool:
        """Record the worker's outcome. False if the reader already gave up."""
        with self._lock:
            kept = not self._resolved
            if kept:
                self._resolved = True
                self._error = error
            self._settled.set()
            return kept


def _failed_job(provider_id: str, error: BaseException) -> Job:
    """A job that is already done and already lost.

    Submission problems are reported this way rather than raised, so the
    executor has one path for "the action did not work" instead of two.
    """
    job = Job(provider_id, deadline=0.0, clock=lambda: 0.0)
    job._resolve(error)
    return job


class ActionRunner(Protocol):
    """How the executor gets an action done."""

    def provider(self, provider_id: str) -> ActionProvider | None: ...

    def submit(
        self,
        provider_id: str,
        params: BaseModel,
        ctx: ActionContext,
        timeout_s: float,
    ) -> Job:
        """Start the action. Returns immediately; never raises."""
        ...

    def close(self) -> None:
        """Release whatever the runner is holding. Idempotent."""
        ...


class ThreadedRunner:
    """The production runner: providers on their own threads, off the loop."""

    def __init__(
        self,
        providers: Iterable[ActionProvider] = (),
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._providers: dict[str, ActionProvider] = {}
        self._lock = threading.Lock()
        self._queues: dict[str, queue.Queue] = {}
        self._threads: dict[str, threading.Thread] = {}
        #: Provider ids with a job in flight. A provider is busy until its
        #: thread returns, including after the job it was running timed out.
        self._busy: set[str] = set()
        self._closing = False

        for provider in providers:
            self.register(provider)

    # ── providers ────────────────────────────────────────────────────────────

    def register(self, provider: ActionProvider) -> None:
        with self._lock:
            self._providers[provider.id] = provider

    def provider(self, provider_id: str) -> ActionProvider | None:
        with self._lock:
            return self._providers.get(provider_id)

    @property
    def provider_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._providers)

    # ── submission ───────────────────────────────────────────────────────────

    def submit(
        self,
        provider_id: str,
        params: BaseModel,
        ctx: ActionContext,
        timeout_s: float,
    ) -> Job:
        with self._lock:
            provider = self._providers.get(provider_id)
            if provider is None:
                return _failed_job(
                    provider_id, ActionUnavailable(f"no provider {provider_id!r} is installed")
                )
            if self._closing:
                return _failed_job(provider_id, ActionUnavailable("the runner is shutting down"))
            if provider_id in self._busy:
                # Only reachable when a previous job was abandoned or timed out
                # and its thread has not come back. Refusing beats queueing: the
                # operator is standing at the arm waiting for an answer, not for
                # the last anchor's work to finish first.
                return _failed_job(
                    provider_id,
                    ActionUnavailable(f"{provider_id} is still busy with an earlier action"),
                )

            job = Job(provider_id, deadline=self._clock() + timeout_s, clock=self._clock)
            self._busy.add(provider_id)
            self._ensure_worker(provider_id)
            self._queues[provider_id].put((job, params, ctx))
            return job

    def _ensure_worker(self, provider_id: str) -> None:
        """Start this provider's thread on first use. Caller holds the lock."""
        if provider_id in self._threads:
            return
        self._queues[provider_id] = queue.Queue()
        thread = threading.Thread(
            target=self._work,
            args=(provider_id,),
            name=f"action-{provider_id}",
            daemon=True,
        )
        self._threads[provider_id] = thread
        thread.start()

    def _work(self, provider_id: str) -> None:
        work = self._queues[provider_id]
        while True:
            item = work.get()
            if item is None:  # shutdown sentinel
                return
            job, params, ctx = item
            try:
                self._run_one(provider_id, job, params, ctx)
            finally:
                with self._lock:
                    self._busy.discard(provider_id)

    def _run_one(self, provider_id: str, job: Job, params: BaseModel, ctx: ActionContext) -> None:
        provider = self.provider(provider_id)
        if provider is None:  # pragma: no cover — unregistered mid-flight
            job._resolve(ActionUnavailable(f"provider {provider_id!r} went away"))
            return

        try:
            provider.run(params, ctx)
        except Exception as exc:
            # Third-party code: anything can come out. The original exception is
            # kept rather than wrapped, so ActionUnavailable still means "every
            # retry fails the same way" by the time the executor sees it.
            log.warning("action %s failed: %s", provider_id, exc, exc_info=True)
            error: BaseException | None = exc
        else:
            error = None

        if not job._resolve(error):
            log.warning(
                "action %s finished after it had already been given up on (%s)",
                provider_id,
                "abandoned" if job.abandoned else "timed out",
            )

    # ── lifecycle ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Stop the workers. A hung provider is joined with a timeout, not
        waited on — that case is the whole reason this module exists."""
        with self._lock:
            self._closing = True
            queues = list(self._queues.items())
            threads = list(self._threads.values())
        for _, work in queues:
            work.put(None)
        for thread in threads:
            thread.join(timeout=SHUTDOWN_JOIN_S)
            if thread.is_alive():
                log.warning("%s did not stop; leaving it to the process exit", thread.name)


class InlineRunner:
    """Runs providers on the calling thread. Tests and tooling only.

    The executor's own behaviour — action ordering, burst pacing, retry policy —
    is about a fake clock and has nothing to do with threads, so the tests that
    cover it use this and stay deterministic. That the real runner keeps the
    control loop free is a separate claim, tested separately against
    :class:`ThreadedRunner`.

    Never used by the service. Blocking here blocks whoever called it, which for
    the control loop is the entire problem.
    """

    def __init__(self, providers: Iterable[ActionProvider] = ()) -> None:
        self._providers = {p.id: p for p in providers}

    def register(self, provider: ActionProvider) -> None:
        self._providers[provider.id] = provider

    def provider(self, provider_id: str) -> ActionProvider | None:
        return self._providers.get(provider_id)

    def submit(
        self,
        provider_id: str,
        params: BaseModel,
        ctx: ActionContext,
        timeout_s: float,
    ) -> Job:
        provider = self._providers.get(provider_id)
        if provider is None:
            return _failed_job(
                provider_id, ActionUnavailable(f"no provider {provider_id!r} is installed")
            )
        try:
            provider.run(params, ctx)
        except Exception as exc:
            return _failed_job(provider_id, exc)
        job = Job(provider_id, deadline=0.0, clock=lambda: 0.0)
        job._resolve(None)
        return job

    def close(self) -> None:
        """Nothing to stop. Here so callers can treat the two runners alike."""


__all__ = [
    "ActionError",
    "ActionRunner",
    "ActionTimeout",
    "ActionUnavailable",
    "InlineRunner",
    "Job",
    "ThreadedRunner",
]
