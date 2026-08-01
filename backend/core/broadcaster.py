"""Fan-out from the control loop to websocket subscribers.

The control loop runs in its own thread at up to 500 Hz; websocket handlers run
on the asyncio event loop. Publishing therefore has to cross that boundary, and
it has to do so without ever blocking the control thread -- a control loop that
stalls because a browser tab stopped reading is a control loop that stops
holding the arm up.

So subscribers get bounded queues and slow ones lose messages rather than
applying back-pressure. These are status updates for a UI; the newest one is
the only one that matters.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

#: Per-subscriber backlog. Small on purpose: a UI that is behind wants the
#: latest state, not a queue of stale ones.
QUEUE_SIZE = 8


class Subscription:
    def __init__(self, queue: asyncio.Queue) -> None:
        self.queue = queue
        self.dropped = 0

    async def get(self) -> Any:
        return await self.queue.get()


class Broadcaster:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[tuple[Subscription, asyncio.AbstractEventLoop]] = []

    def subscribe(self, loop: asyncio.AbstractEventLoop | None = None) -> Subscription:
        sub = Subscription(asyncio.Queue(maxsize=QUEUE_SIZE))
        with self._lock:
            self._subs.append((sub, loop or asyncio.get_running_loop()))
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subs = [(s, loop) for s, loop in self._subs if s is not sub]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, message: Any) -> None:
        """Hand ``message`` to every subscriber. Never blocks, never raises."""
        with self._lock:
            targets = list(self._subs)

        for sub, loop in targets:
            try:
                loop.call_soon_threadsafe(self._offer, sub, message)
            except RuntimeError:
                # Event loop already closed — the handler is on its way out.
                self.unsubscribe(sub)

    @staticmethod
    def _offer(sub: Subscription, message: Any) -> None:
        try:
            sub.queue.put_nowait(message)
        except asyncio.QueueFull:
            # Drop the oldest so the subscriber converges on current state
            # instead of replaying history it no longer cares about.
            try:
                sub.queue.get_nowait()
                sub.queue.put_nowait(message)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                pass
            sub.dropped += 1
