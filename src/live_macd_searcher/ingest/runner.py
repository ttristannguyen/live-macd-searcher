"""Keeping closed candles flowing across disconnects (DESIGN §6, "Reconnects").

One session is one connection's lifetime:

1. Connect and subscribe. Messages from here on are buffered, so nothing is missed.
2. Gap-fill over REST: everything since each symbol's last closed bar — or the whole
   warm-up on a cold start — offered to the closer in order.
3. Drain the buffer, then keep streaming until the connection drops.

Overlap between steps 2 and 3 is harmless: the closer ignores hours already closed and
keeps the most complete snapshot of the current one, and the detector ignores any bar
it has already seen.

Every bar the *websocket* closes is confirmed by REST before it is delivered (PLAN D14):
its last snapshot of an hour can miss the hour's final trade. Bars the gap-fill closes
are REST's own already. The loop is sequential, so delivery order per symbol is the
order of the hours, whatever REST's pacing costs; the pump keeps reading the stream
meanwhile, so the connection never stalls.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from ..detect.config import (
    BACKFILL_BARS,
    CLOCK_SKEW_MARGIN_SECONDS,
    RECONNECT_MAX_BACKOFF_SECONDS,
)
from ..market import Candle
from .closer import CandleCloser, confirm
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
        self._buffer: asyncio.Queue | None = None  # the current session's, while one runs
        self.last_delivered: dict[str, Candle] = {}  # the last bar `on_closed` received
        self.bars_corrected = 0  # websocket bars REST corrected (PLAN D14), for health
        self.has_streamed = False  # any session has finished its gap-fill: past starting up
        self.last_message_at: float | None = None  # wall clock, for health only

    def resubscribe(self, symbols: list[str]) -> None:
        """Change the subscription list. The current session ends, and `run()` reconnects
        with the new list: a new symbol gets a cold start, the rest gap-fill as usual.

        Every name must be one the exchange lists — one that isn't drops the whole
        connection (DESIGN §6).
        """
        self.symbols = symbols
        if self._buffer is not None:
            self._buffer.put_nowait(FeedDisconnected("resubscribing"))

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
            log.info("connected: %d symbols subscribed", len(self.symbols))
            buffer: asyncio.Queue[CandleMessage | BaseException] = asyncio.Queue()
            self._buffer = buffer
            pump = asyncio.create_task(_pump(messages, buffer))
            try:
                await self._gap_fill()
                self.streaming = self.last_session_streamed = self.has_streamed = True
                while True:
                    item = await buffer.get()
                    if isinstance(item, BaseException):
                        raise item
                    self.last_message_at = time.time()
                    symbol, candle = item
                    released = self.closer.offer(symbol, candle)
                    if released:
                        self._deliver(symbol, await self._confirm(symbol, released))
            finally:
                self.streaming = False
                self._buffer = None
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)

    async def _gap_fill(self) -> None:
        end = self.now_ms()
        started, closed, fetched = time.monotonic(), 0, 0
        for symbol in self.symbols:
            start = self._resume(symbol, end)
            if not _hour_closed_since(start, end):
                continue  # nothing missed: the stream resends the forming hour in full
            fetched += 1
            for candle in await self.feed.candles(symbol, start, end):
                bars = self.closer.offer(symbol, candle)
                self._deliver(symbol, bars)  # closed by REST candles: REST's own already
                closed += len(bars)
        log.info("gap-filled %d closed bars from %d of %d symbols in %.0fs",
                 closed, fetched, len(self.symbols), time.monotonic() - started)  # fmt: skip

    def _resume(self, symbol: str, end: int) -> int:
        """Where a symbol's gap-fill starts: just after the last bar *delivered*.

        Never after one the closer merely released: a confirmation that failed leaves
        bars released but undelivered, and resuming from the closer would lose them. So
        if the closer is ahead of what was delivered (or knows nothing, after a
        restart), it is rewound to the last delivered bar — which also lets hours with no
        trades right after that bar be filled at its close rather than skipped.
        """
        last = self.last_delivered.get(symbol) or self.last_stored(symbol)
        if last is not None:
            if self.closer.last_closed.get(symbol) != last:
                self.closer.last_closed[symbol] = last
                self.closer.forming.pop(symbol, None)  # the gap-fill re-supplies it
            return last.open_time + HOUR_MS
        # Cold start: exactly a full warm-up. Hyperliquid returns every candle that
        # overlaps the range, so starting on an hour boundary BACKFILL_BARS hours back
        # gives BACKFILL_BARS closed candles plus the forming one.
        return (end // HOUR_MS - BACKFILL_BARS) * HOUR_MS

    async def _confirm(self, symbol: str, released: list[Candle]) -> list[Candle]:
        """REST's record of the hours the websocket just closed (PLAN D14). Paced like every
        REST call; a failure ends the session, and the next gap-fill fetches them again."""
        rest = await self.feed.candles(symbol, released[0].open_time, released[-1].open_time)
        confirmed = confirm(released, rest)
        rest_hours = {candle.open_time for candle in rest}
        for streamed, final in zip(released, confirmed, strict=True):
            if final != streamed:
                self.bars_corrected += 1
                log.info("%s %d: REST corrected the websocket bar (%s -> %s)",
                         symbol, streamed.open_time, streamed, final)  # fmt: skip
            elif streamed.volume > 0 and streamed.open_time not in rest_hours:
                log.warning("%s %d: REST has no candle for a traded hour; keeping the "
                            "websocket bar", symbol, streamed.open_time)  # fmt: skip
        return confirmed

    def _deliver(self, symbol: str, bars: list[Candle]) -> None:
        for bar in bars:
            self.on_closed(symbol, bar)
            self.last_delivered[symbol] = bar


def _hour_closed_since(start: int, now: int) -> bool:
    """Whether an hour at or after `start` may have closed by `now` — so REST holds a
    closed bar the stream can no longer supply.

    The websocket sends each hour's *whole* candle on every update, so if the only hour
    since `start` is the one still forming, nothing was missed. Near the hour boundary,
    where a slightly slow clock could be wrong about which hour is forming, assume one
    has closed: fetching is always safe; skipping is only safe when sure.
    """
    forming = now // HOUR_MS * HOUR_MS
    return start < forming or now - forming < CLOCK_SKEW_MARGIN_SECONDS * 1000


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
