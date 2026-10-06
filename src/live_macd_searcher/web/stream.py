"""The live stream: server-sent events from the detector to every open board (DESIGN §8).

Everything here is best-effort *on purpose* — the one place in the app where it is
(CLAUDE.md guardrails). The detector publishes with a non-blocking put into each
client's own bounded queue and never waits on a browser. A client that falls too far
behind is dropped; its browser reconnects by itself and refetches the board.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict

from ..detect.config import SSE_CLIENT_BACKLOG, SSE_HEARTBEAT_SECONDS
from ..detect.symbol_state import Provisional
from ..detect.window import WindowEvent
from .models import WindowOut


class Subscriber:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=SSE_CLIENT_BACKLOG)
        self.dropped = False


class Broadcaster:
    def __init__(self) -> None:
        self.subscribers: set[Subscriber] = set()

    def subscribe(self) -> Subscriber:
        subscriber = Subscriber()
        self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self.subscribers.discard(subscriber)

    def publish(self, event: str, data: str) -> None:
        """Hand one event to every client. Never blocks, never raises."""
        for subscriber in list(self.subscribers):
            try:
                subscriber.queue.put_nowait((event, data))
            except asyncio.QueueFull:
                # Deliberately swallowed: a browser this far behind is dropped, never
                # waited on — the detector must not slow down for a client. Its stream
                # ends, and EventSource reconnects and refetches the board (DESIGN §8).
                subscriber.dropped = True
                self.subscribers.discard(subscriber)

    def window(self, event: WindowEvent, window_id: int) -> None:
        """A window event, in the same shape `/api/windows` returns, so rows update in place."""
        payload = WindowOut(id=window_id, **asdict(event.window))
        self.publish(f"window.{event.kind}", payload.model_dump_json())

    def provisional(self, readings: list[Provisional]) -> None:
        """One refresh's forming-bar readings: displayed, never persisted (invariant 1)."""
        self.publish("tick.provisional", json.dumps({"readings": [asdict(r) for r in readings]}))


async def event_stream(
    broadcaster: Broadcaster,
    subscriber: Subscriber,
    heartbeat: float = SSE_HEARTBEAT_SECONDS,
    health: Callable[[], str] | None = None,
) -> AsyncIterator[str]:
    """One client's SSE body. Ends when the client goes or is dropped as too slow.

    With `health` (a function returning the /api/health JSON), every heartbeat is a
    `health` event rather than a bare comment: the board learns the feed went stale
    without ever polling.
    """

    def beat() -> str:
        # Keeps an idle connection open through the Tailscale proxy (DESIGN §13).
        return f"event: health\ndata: {health()}\n\n" if health else ": keep-alive\n\n"

    try:
        yield ": connected\n\n"  # opens the stream at once, through any proxy buffering
        if health:
            yield beat()
        while not subscriber.dropped:
            try:
                event, data = await asyncio.wait_for(subscriber.queue.get(), timeout=heartbeat)
            except TimeoutError:
                yield beat()
                continue
            yield f"event: {event}\ndata: {data}\n\n"
    finally:
        broadcaster.unsubscribe(subscriber)
