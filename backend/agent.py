"""Exclusive control leases for external agents.

An outside caller — an LLM tool, a shot-list script — can take control of the
arm, but only one at a time and only for as long as it keeps talking.

Three rules, each answering a specific way this goes wrong:

**Exclusive.** Two callers interleaving joint commands on one arm produces
motion neither asked for. Acquiring while held is a 409, not a queue.

**Expiring.** An agent that crashes mid-shoot would otherwise hold the arm
until someone notices. The lease dies on its own: after an idle timeout with no
commands, and at a hard ceiling regardless of activity.

**Revocable.** The person standing next to the arm outranks the process
controlling it. The UI can force-release without the token.

The lease grants *control*, never *safety*. Every agent motion endpoint sits
behind the same emergency-stop gate as the UI, and a latched stop refuses an
agent exactly as it refuses a person.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

#: No command for this long and the lease lapses. Short enough that a crashed
#: agent frees the arm before anyone gets impatient.
IDLE_TTL_S = 5 * 60
#: Ceiling regardless of activity, so a stuck loop cannot hold the arm forever.
HARD_TTL_S = 30 * 60


@dataclass(frozen=True)
class LeaseInfo:
    held: bool
    owner: str | None = None
    acquired_at: float | None = None
    last_command_at: float | None = None
    expires_in_s: float | None = None


class AgentLease:
    """The single control lease. Thread-safe; the API and the loop both read it."""

    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        idle_ttl_s: float = IDLE_TTL_S,
        hard_ttl_s: float = HARD_TTL_S,
    ) -> None:
        self._clock = clock or time.monotonic
        self._idle_ttl = idle_ttl_s
        self._hard_ttl = hard_ttl_s
        self._lock = threading.RLock()

        self._token: str | None = None
        self._owner: str | None = None
        self._acquired_at: float | None = None
        self._last_command_at: float | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def acquire(self, owner: str) -> str:
        """Take the lease. Raises ``RuntimeError`` if someone else holds it."""
        with self._lock:
            self._expire_if_stale()
            if self._token is not None:
                raise RuntimeError(f"control is held by {self._owner!r}")

            now = self._clock()
            self._token = secrets.token_urlsafe(24)
            self._owner = owner
            self._acquired_at = now
            self._last_command_at = now
            log.info("agent %r acquired control", owner)
            return self._token

    def release(self, token: str | None = None, force: bool = False) -> bool:
        """Give the lease up. Returns whether one was held.

        ``force`` skips the token check: the person at the arm outranks the
        process controlling it, and they will not have its token.
        """
        with self._lock:
            if self._token is None:
                return False
            if not force and token != self._token:
                raise PermissionError("token does not hold control")

            log.info("agent %r released control%s", self._owner, " (forced)" if force else "")
            self._token = self._owner = None
            self._acquired_at = self._last_command_at = None
            return True

    def check(self, token: str | None) -> None:
        """Validate a token and refresh the idle timer. Raises on any problem."""
        with self._lock:
            self._expire_if_stale()
            if self._token is None:
                raise PermissionError("no agent holds control; acquire first")
            if token != self._token:
                raise PermissionError("token does not hold control")
            self._last_command_at = self._clock()

    def info(self) -> LeaseInfo:
        with self._lock:
            self._expire_if_stale()
            if self._token is None:
                return LeaseInfo(held=False)

            now = self._clock()
            # `or now` would be wrong here: a timestamp of exactly 0.0 is falsy,
            # which silently turns every elapsed time into zero and stops the
            # lease ever expiring. Real clocks rarely hit 0, so this hides.
            last = self._last_command_at if self._last_command_at is not None else now
            since = self._acquired_at if self._acquired_at is not None else now
            return LeaseInfo(
                held=True,
                owner=self._owner,
                acquired_at=self._acquired_at,
                last_command_at=self._last_command_at,
                expires_in_s=min(
                    self._idle_ttl - (now - last),
                    self._hard_ttl - (now - since),
                ),
            )

    @property
    def is_held(self) -> bool:
        with self._lock:
            self._expire_if_stale()
            return self._token is not None

    @property
    def idle_timeout_s(self) -> float:
        """The idle TTL, public so the acquire response can state it outright."""
        return self._idle_ttl

    # ── internals ────────────────────────────────────────────────────────────

    def _expire_if_stale(self) -> None:
        """Drop a lapsed lease. Called on every read, so expiry needs no timer
        thread and cannot drift from what callers observe."""
        if self._token is None:
            return

        now = self._clock()
        # Explicit None checks, not truthiness: a timestamp of 0.0 is falsy.
        last = self._last_command_at if self._last_command_at is not None else now
        since = self._acquired_at if self._acquired_at is not None else now
        idle = now - last
        held = now - since

        if idle >= self._idle_ttl:
            log.warning("agent %r lease lapsed after %.0fs idle", self._owner, idle)
        elif held >= self._hard_ttl:
            log.warning("agent %r lease hit the %.0fs ceiling", self._owner, self._hard_ttl)
        else:
            return

        self._token = self._owner = None
        self._acquired_at = self._last_command_at = None
