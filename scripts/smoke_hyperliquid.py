"""Smoke-test the live Hyperliquid feed, so an API change shows up before a deploy.

    uv run python scripts/smoke_hyperliquid.py          # universe + 30 s of live candles
    uv run python scripts/smoke_hyperliquid.py --warm   # warm the whole universe (~7 min)

`--warm` is PLAN M4's live gate: every symbol gets a full warm-up through the real
pacer, with no window emitted and no 429. It spends REST budget on *this* machine's IP,
so run it from a desktop, not from the droplet `macd_searcher` shares.
"""

import argparse
import asyncio
import logging
import time
from collections import Counter

import httpx

from live_macd_searcher.detect.config import (
    BACKFILL_BARS,
    BOLLINGER_PERIOD,
    BOLLINGER_WIDTH,
    REST_WEIGHT_PER_MIN,
)
from live_macd_searcher.detect.detector import BarReading, WindowDetector
from live_macd_searcher.indicators.bollinger import BollingerState
from live_macd_searcher.indicators.macd import MacdState
from live_macd_searcher.ingest.feed import HOUR_MS, FeedError
from live_macd_searcher.ingest.hyperliquid import HyperliquidFeed
from live_macd_searcher.ingest.pacer import RestPacer
from live_macd_searcher.ingest.runner import Ingest


async def peek(feed: HyperliquidFeed, symbols: list[str]) -> None:
    """Print 30 seconds of live candles for one core and one xyz symbol."""
    sample = [symbols[0]] + [s for s in symbols if s.startswith("xyz:")][:1]
    print(f"subscribing to {sample}")
    started = time.monotonic()
    async with feed.connect(sample) as messages:
        async for symbol, candle in messages:
            print(f"{time.monotonic() - started:5.1f}s  {symbol:10} {candle}")
            if time.monotonic() - started > 30:
                return


async def warm(feed: HyperliquidFeed, symbols: list[str]) -> None:
    """Warm every symbol through the real pacer, then report."""
    state = {s: (MacdState(), BollingerState(BOLLINGER_PERIOD, BOLLINGER_WIDTH), WindowDetector(s))
             for s in symbols}  # fmt: skip
    closed: dict[str, list[int]] = {s: [] for s in symbols}  # open_times, in order
    events: dict[str, list] = {s: [] for s in symbols}

    def on_closed(symbol, candle):
        macd, bands, detector = state[symbol]
        point = macd.update(candle.close)
        reading = BarReading(
            candle.open_time, candle.high, candle.low, candle.close,
            point.macd, point.signal, point.hist, bands.update(candle.close),
        )  # fmt: skip
        closed[symbol].append(candle.open_time)
        events[symbol] += detector.step(reading)

    ingest = Ingest(feed, symbols, on_closed)
    started, started_hour = time.monotonic(), int(time.time() * 1000) // HOUR_MS
    session = asyncio.create_task(ingest.run_session())
    while not ingest.streaming and not session.done():
        await asyncio.sleep(1)
    elapsed = time.monotonic() - started
    session.cancel()
    if session.done() and not session.cancelled() and session.exception():
        raise session.exception()
    hours_crossed = int(time.time() * 1000) // HOUR_MS - started_hour

    counts = Counter(len(times) for times in closed.values())
    odd = {s: len(t) for s, t in closed.items() if len(t) != BACKFILL_BARS + hours_crossed}
    # The real warm-up invariant: no window may have its peak before the bar that made
    # its symbol warm. A window that opens after that, on backfilled bars, is legitimate.
    too_early = [
        e.window for s in symbols for e in events[s]
        if len(closed[s]) >= BACKFILL_BARS and e.window.started_at < closed[s][BACKFILL_BARS - 1]
    ]  # fmt: skip
    print(f"\nwarmed {len(symbols)} symbols in {elapsed / 60:.1f} min "
          f"(pacer capped at {REST_WEIGHT_PER_MIN} weight/min; hour boundaries crossed: "
          f"{hours_crossed})")  # fmt: skip
    print(f"closed bars per symbol: {dict(sorted(counts.items()))}")
    print(f"symbols without exactly {BACKFILL_BARS + hours_crossed}: {odd or 'none'}")
    print(f"window events from warm bars: {sum(len(e) for e in events.values())}")
    print(f"windows with a peak before warm-up ended: {len(too_early)}  (must be 0)")
    print(f"REST 429s: {feed.rate_limited}  (must be 0)")


async def main(warm_up: bool) -> None:
    async with httpx.AsyncClient() as client:
        feed = HyperliquidFeed(client, RestPacer(REST_WEIGHT_PER_MIN))
        symbols = (await feed.universe()).tradeable
        classes = Counter("xyz" if ":" in s else "core" for s in symbols)
        print(f"universe: {len(symbols)} symbols {dict(classes)}")
        try:
            await (warm(feed, symbols) if warm_up else peek(feed, symbols))
        except FeedError as exc:
            raise SystemExit(f"feed failed: {exc}") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--warm", action="store_true", help="warm the whole universe")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main(parser.parse_args().warm))
