"""Keeping closed candles flowing across disconnects (DESIGN §6, "Reconnects").

One session is one connection's lifetime:

1. Connect and subscribe. Messages from here on are buffered, so nothing is missed.
2. Gap-fill over REST: everything since each symbol's last closed bar — or the whole
   warm-up on a cold start — offered to the closer in order.
3. Drain the buffer, then keep streaming until the connection drops.

Overlap between steps 2 and 3 is harmless: the closer ignores hours already closed and
keeps the most complete snapshot of the current one, and the detector ignores any bar
it has already seen.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from ..detect.config import BACKFILL_BARS, RECONNECT_MAX_BACKOFF_SECONDS
from ..market import Candle
from .closer import CandleCloser
from .feed import HOUR_MS, CandleMessage, FeedDisconnected, FeedError, MarketFeed

log = logging.getLogger(__name__)


class Ingest:
    def __init__(
        self,
        feed: MarketFeed,
        symbols: list[str],
        on_closed: Callable[[str, Candle], None],
        *,
        last_stored: Callable[[str], Candle | None] = lambda symbol: None,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`on_closed` receives every closed candle, in order per symbol.

        `last_stored` gives the last closed bar already stored for a symbol (None if
        there is none): the gap-fill resumes after it. The wall clock `now_ms` is only used
        to choose REST request ranges, never to decide that a bar has closed.
        """
        self.feed = feed
        self.symbols = symbols
        self.on_closed = on_closed
        self.last_stored = last_stored
        self.now_ms = now_ms
        self._sleep = sleep
        self.closer = CandleCloser()
        self.streaming = False  # gap-fill done and the live stream flowing
        self.last_session_streamed = False  # a session that got that far resets the backoff

    async def run(self) -> None:
        """Run sessions forever, backing off between them. Only `FeedError`s are retried:
        anything else is a bug and propagates."""
        backoff = 1.0
        while True:
            try:
                await self.run_session()
            except FeedError as exc:
                log.warning("feed session ended: %s", exc)
            if self.last_session_streamed:
                backoff = 1.0  # it was healthy; this is a fresh outage
            log.info("reconnecting in %.0fs", backoff)
            await self._sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_BACKOFF_SECONDS)

    async def run_session(self) -> None:
        """One connection: subscribe, gap-fill, stream. Ends by raising `FeedError`."""
        self.last_session_streamed = False
        async with self.feed.connect(self.symbols) as messages:
            buffer: asyncio.Queue[CandleMessage | BaseException] = asyncio.Queue()
            pump = asyncio.create_task(_pump(messages, buffer))
            try:
                await self._gap_fill()
                self.streaming = self.last_session_streamed = True
                while True:
                    item = await buffer.get()
                    if isinstance(item, BaseException):
                        raise item
                    self._offer(*item)
            finally:
                self.streaming = False
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)

    async def _gap_fill(self) -> None:
        end = self.now_ms()
        for symbol in self.symbols:
            for candle in await self.feed.candles(symbol, self._gap_start(symbol, end), end):
                self._offer(symbol, candle)

    def _gap_start(self, symbol: str, end: int) -> int:
        held = self.closer.forming.get(symbol)
        if held is not None:
            return held.open_time  # the hour that was forming when the connection dropped
        last = self.closer.last_closed.get(symbol) or self.last_stored(symbol)
        if last is not None:
            # Seed the closer, so hours with no trades right after this bar are filled at
            # its close rather than skipped.
            self.closer.last_closed[symbol] = last
            return last.open_time + HOUR_MS
        # Cold start: exactly a full warm-up. Hyperliquid returns every candle that
        # overlaps the range, so starting on an hour boundary BACKFILL_BARS hours back
        # gives BACKFILL_BARS closed candles plus the forming one.
        return (end // HOUR_MS - BACKFILL_BARS) * HOUR_MS

    def _offer(self, symbol: str, candle: Candle) -> None:
        for closed in self.closer.offer(symbol, candle):
            self.on_closed(symbol, closed)


async def _pump(messages: AsyncIterator[CandleMessage], buffer: asyncio.Queue) -> None:
    """Move stream messages into `buffer` while the gap-fill runs.

    Whatever ends the stream is forwarded, not swallowed: the session re-raises it. A
    `FeedError` means reconnect; anything else is a bug and must surface.
    """
    try:
        async for message in messages:
            buffer.put_nowait(message)
        buffer.put_nowait(FeedDisconnected("stream ended"))
    except Exception as exc:
        buffer.put_nowait(exc)
