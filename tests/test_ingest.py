"""The ingest loop, driven offline by a scripted feed (PLAN M4).

The central property: a connection that drops mid-hour and comes back later produces
exactly the same closed bars, and so exactly the same windows, as one that never
dropped.
"""

import asyncio
import math
from collections import defaultdict

import pytest

from live_macd_searcher.detect.config import BACKFILL_BARS
from live_macd_searcher.ingest.closer import CandleCloser
from live_macd_searcher.ingest.feed import HOUR_MS, FeedDisconnected
from live_macd_searcher.ingest.runner import Ingest
from live_macd_searcher.market import Candle
from tests.fakes import FakeFeed, NoMoreSessions, Session, partial, stream_hours
from tests.pipeline import Pipeline

SYMBOLS = ["BTC", "xyz:GOLD"]
HOURS = 700


def synthetic_history(phase: float) -> list[Candle]:
    candles, previous = [], 100.0
    for hour in range(HOURS):
        close = round(100 + 6 * math.sin(hour / 9 + phase) + 2.5 * math.sin(hour / 2.7), 2)
        high, low = max(previous, close) + 0.3, min(previous, close) - 0.3
        candles.append(Candle(hour * HOUR_MS, previous, high, low, close, 100.0 + hour))
        previous = close
    return candles


HISTORY = {symbol: synthetic_history(phase) for phase, symbol in enumerate(SYMBOLS)}


class Recorder:
    """The `on_closed` sink: every closed bar, and what the detector made of them.

    Kept per symbol. Order *across* symbols means nothing — a gap-fill replays one
    symbol's missed hours before the next's, where streaming interleaves them — but
    order within a symbol is the whole point.
    """

    def __init__(self) -> None:
        self.closed: dict[str, list[Candle]] = defaultdict(list)
        self.events: dict[str, list] = defaultdict(list)
        self.pipelines = {symbol: Pipeline(symbol) for symbol in SYMBOLS}

    def on_closed(self, symbol: str, candle: Candle) -> None:
        self.closed[symbol].append(candle)
        self.events[symbol] += self.pipelines[symbol].step(candle)


def drive(sessions: list[Session], **ingest_options) -> tuple[Recorder, FakeFeed]:
    feed = FakeFeed(HISTORY, sessions)
    recorder = Recorder()
    ingest = Ingest(feed, SYMBOLS, recorder.on_closed, now_ms=feed.now_ms, **ingest_options)

    async def every_session():
        for _ in sessions:
            with pytest.raises(FeedDisconnected):
                await ingest.run_session()

    asyncio.run(every_session())
    return recorder, feed


# --- the closer --------------------------------------------------------------------


def test_closer_releases_a_candle_only_when_a_later_hour_arrives():
    closer = CandleCloser()
    hour0, hour1 = HISTORY["BTC"][0], HISTORY["BTC"][1]
    assert closer.offer("BTC", partial(hour0)) == []
    assert closer.offer("BTC", hour0) == []
    assert closer.offer("BTC", hour1) == [hour0]
    assert closer.forming["BTC"] == hour1


def test_closer_keeps_the_more_complete_snapshot_whatever_the_order():
    closer = CandleCloser()
    hour0, hour1 = HISTORY["BTC"][0], HISTORY["BTC"][1]
    closer.offer("BTC", hour0)
    closer.offer("BTC", partial(hour0))  # an older snapshot arriving late
    assert closer.offer("BTC", hour1) == [hour0]


def test_closer_ignores_an_hour_that_has_already_closed():
    closer = CandleCloser()
    closer.offer("BTC", HISTORY["BTC"][0])
    closer.offer("BTC", HISTORY["BTC"][1])
    assert closer.offer("BTC", HISTORY["BTC"][0]) == []
    assert closer.forming["BTC"] == HISTORY["BTC"][1]


def flat(hour: int, price: float) -> Candle:
    return Candle(hour * HOUR_MS, price, price, price, price, 0.0)


def test_closer_fills_hours_with_no_trades_with_flat_bars_at_the_previous_close():
    # Hyperliquid sends no candle for an hour with no trades (PLAN D13).
    closer = CandleCloser()
    hour0, hour3 = HISTORY["BTC"][0], HISTORY["BTC"][3]
    closer.offer("BTC", hour0)
    assert closer.offer("BTC", hour3) == [hour0, flat(1, hour0.close), flat(2, hour0.close)]


def test_closer_fills_a_gap_after_the_last_stored_bar():
    # After a restart: the last stored bar is hour 0, and the first fetched is hour 2.
    closer = CandleCloser()
    hour0, hour2, hour3 = HISTORY["BTC"][0], HISTORY["BTC"][2], HISTORY["BTC"][3]
    closer.last_closed["BTC"] = hour0
    closer.offer("BTC", hour2)
    assert closer.offer("BTC", hour3) == [flat(1, hour0.close), hour2]


# --- sessions ----------------------------------------------------------------------


def test_cold_start_backfills_exactly_a_full_warm_up():
    recorder, _ = drive([Session(now_hour=450, messages=[])])
    for symbol in SYMBOLS:
        assert recorder.closed[symbol] == HISTORY[symbol][450 - BACKFILL_BARS : 450]
    assert not any(recorder.events.values())  # nothing is emitted while warming


def test_streamed_bars_close_with_their_final_snapshot_not_a_partial():
    recorder, _ = drive([Session(now_hour=450, messages=stream_hours(HISTORY, 450, 460))])
    # Hours 450..459 closed with their final snapshots; 460 is still forming, so held.
    assert recorder.closed["BTC"][-10:] == HISTORY["BTC"][450:460]


def test_resumes_after_the_last_stored_bar():
    recorder, feed = drive(
        [Session(now_hour=450, messages=[])],
        last_stored=lambda symbol: HISTORY[symbol][440],
    )
    assert {start for _, start, _ in feed.requests} == {441 * HOUR_MS}
    assert recorder.closed["BTC"] == HISTORY["BTC"][441:450]


class QuietHourFeed(FakeFeed):
    """BTC has no trades in hour 455: no candle over REST or the websocket."""

    QUIET = 455 * HOUR_MS

    async def candles(self, symbol, start_ms, end_ms):
        candles = await super().candles(symbol, start_ms, end_ms)
        return [c for c in candles if not (symbol == "BTC" and c.open_time == self.QUIET)]

    async def _replay(self, messages):
        async for symbol, candle in super()._replay(messages):
            if not (symbol == "BTC" and candle.open_time == self.QUIET):
                yield symbol, candle


def test_a_quiet_hour_in_the_stream_becomes_a_flat_bar():
    feed = QuietHourFeed(HISTORY, [Session(now_hour=450, messages=stream_hours(HISTORY, 450, 460))])
    recorder = Recorder()
    ingest = Ingest(feed, SYMBOLS, recorder.on_closed, now_ms=feed.now_ms)
    with pytest.raises(FeedDisconnected):
        asyncio.run(ingest.run_session())

    times = [c.open_time // HOUR_MS for c in recorder.closed["BTC"]]
    assert times == list(range(50, 460))  # no hour missing
    assert recorder.closed["BTC"][455 - 50] == flat(455, HISTORY["BTC"][454].close)
    assert recorder.closed["xyz:GOLD"][-10:] == HISTORY["xyz:GOLD"][450:460]  # untouched


def test_a_drop_mid_hour_gives_the_same_bars_and_windows_as_no_drop():
    uninterrupted, _ = drive([Session(now_hour=450, messages=stream_hours(HISTORY, 450, 699))])

    # Drops after hour 520's partial snapshot and is down until hour 530. On reconnect,
    # a stale message for an hour REST already covered arrives first.
    stale = [(symbol, HISTORY[symbol][529]) for symbol in SYMBOLS]
    interrupted, feed = drive([
        Session(now_hour=450, messages=stream_hours(HISTORY, 450, 520, cut_after_partial=True)),
        Session(now_hour=530, messages=stale + stream_hours(HISTORY, 530, 699)),
    ])  # fmt: skip

    assert interrupted.closed == uninterrupted.closed
    assert interrupted.events == uninterrupted.events
    # Not vacuous: windows were produced, and resolved, after warm-up.
    assert any(e.kind == "resolved" for events in uninterrupted.events.values() for e in events)
    # The reconnect gap-filled from the hour that was forming when it dropped.
    assert {start for symbol, start, _ in feed.requests[len(SYMBOLS):]} == {520 * HOUR_MS}


# --- reconnecting ------------------------------------------------------------------


def run_until_sessions_run_out(feed: FakeFeed) -> list[float]:
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    ingest = Ingest(feed, SYMBOLS, lambda symbol, candle: None, now_ms=feed.now_ms,
                    sleep=record_sleep)  # fmt: skip
    with pytest.raises(NoMoreSessions):
        asyncio.run(ingest.run())
    return delays


def test_healthy_sessions_reconnect_after_a_short_pause():
    feed = FakeFeed(HISTORY, [Session(450, []), Session(451, []), Session(452, [])])
    assert run_until_sessions_run_out(feed) == [1.0, 1.0, 1.0]


class RefusingFeed(FakeFeed):
    """Every connection attempt fails before streaming, until the sessions run out."""

    def __init__(self, failures: int) -> None:
        super().__init__(HISTORY, [Session(450, [])])
        self.failures = failures

    def connect(self, symbols):
        if self.failures:
            self.failures -= 1
            raise FeedDisconnected("refused")
        raise NoMoreSessions


def test_failing_connections_back_off_exponentially_up_to_a_cap():
    delays = run_until_sessions_run_out(RefusingFeed(failures=8))
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


class BuggyFeed(FakeFeed):
    async def _replay(self, messages):
        raise ValueError("a bug, not an outage")
        yield  # pragma: no cover — makes this an async generator


def test_a_bug_in_the_stream_surfaces_instead_of_being_retried():
    feed = BuggyFeed(HISTORY, [Session(450, [])])
    with pytest.raises(ValueError, match="a bug"):
        asyncio.run(Ingest(feed, SYMBOLS, lambda s, c: None, now_ms=feed.now_ms).run())
