"""Hyperliquid: REST for the universe and history, one websocket for live candles.

Request shapes and market filters follow `macd_searcher`'s working client. Facts
learned by probing the live API on 2026-10-05, where they differ from or go beyond
the docs:

- Candle prices and volume arrive as strings, over REST and websocket alike.
- Subscribing sends no snapshot: a symbol's first message comes with its next trade.
- `xyz:` symbols work on the websocket exactly as core ones do.
- Subscribing to a coin that doesn't exist drops the *whole* connection. Only ever
  subscribe to names `universe()` returned.
"""

import asyncio
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
import websockets

from ..detect.config import (
    MIN_DAY_VOLUME_USD,
    MIN_OPEN_INTEREST_USD,
    REST_ATTEMPTS,
    REST_TIMEOUT_SECONDS,
    WS_PING_SECONDS,
    WS_SUBSCRIBE_TIMEOUT_SECONDS,
)
from ..market import Candle
from .feed import HOUR_MS, CandleMessage, FeedDisconnected, FeedError
from .pacer import RestPacer

log = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"

# Rate-limit weights, from Hyperliquid's docs: every info request we make costs 20, and
# candleSnapshot costs extra per 60 candles returned. Rounding up overcharges slightly,
# which is the safe direction for a shared budget.
INFO_WEIGHT = 20
CANDLES_PER_EXTRA_WEIGHT = 60

EXTRA_DEXES = ("xyz",)  # HIP-3 DEXes in the universe, beyond the core perps
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
PING = json.dumps({"method": "ping"})


class HyperliquidFeed:
    def __init__(
        self,
        client: httpx.AsyncClient,
        pacer: RestPacer,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.pacer = pacer
        self._sleep = sleep
        self.rate_limited = 0  # 429s received; reported in /api/health

    # --- REST --------------------------------------------------------------------------

    async def universe(self) -> list[str]:
        symbols: list[str] = []
        for dex in (None, *EXTRA_DEXES):
            body = {"type": "metaAndAssetCtxs"} | ({"dex": dex} if dex else {})
            meta, contexts = await self._info(body, INFO_WEIGHT)
            for market, context in zip(meta["universe"], contexts, strict=True):
                if _tradeable(market, context):
                    symbols.append(market["name"])  # xyz names arrive prefixed: "xyz:TSLA"
        return symbols

    async def candles(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        expected = (end_ms - start_ms) // HOUR_MS + 1
        weight = INFO_WEIGHT + math.ceil(expected / CANDLES_PER_EXTRA_WEIGHT)
        body = {
            "type": "candleSnapshot",
            "req": {"coin": symbol, "interval": "1h", "startTime": start_ms, "endTime": end_ms},
        }
        raw = await self._info(body, weight)
        return sorted((_candle(c) for c in raw), key=lambda candle: candle.open_time)

    async def _info(self, body: dict, weight: int):
        """POST /info, paced, retrying 429s, 5xxs, and network errors with backoff."""
        problem: Exception | None = None
        for attempt in range(1, REST_ATTEMPTS + 1):
            await self.pacer.spend(weight)  # every attempt, retries included
            try:
                response = await self.client.post(INFO_URL, json=body, timeout=REST_TIMEOUT_SECONDS)
            except httpx.TransportError as exc:
                problem = exc
            else:
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 429:
                    self.rate_limited += 1
                problem = FeedError(
                    f"HTTP {response.status_code} on {body['type']}: {response.text[:200]}"
                )
                if response.status_code not in RETRYABLE_STATUS:
                    raise problem
            if attempt < REST_ATTEMPTS:
                delay = 2.0 ** (attempt - 1)
                log.warning("%s attempt %d failed (%s); retrying in %.0fs",
                            body["type"], attempt, problem, delay)  # fmt: skip
                await self._sleep(delay)
        raise FeedError(f"{body['type']} failed after {REST_ATTEMPTS} attempts") from problem

    # --- websocket ---------------------------------------------------------------------

    @asynccontextmanager
    async def connect(self, symbols: list[str]) -> AsyncIterator[AsyncIterator[CandleMessage]]:
        try:
            ws = await websockets.connect(WS_URL)
        except (websockets.WebSocketException, OSError) as exc:
            raise FeedDisconnected(f"could not connect: {exc!r}") from exc
        try:
            try:
                for symbol in symbols:
                    subscription = {"type": "candle", "coin": symbol, "interval": "1h"}
                    await ws.send(json.dumps({"method": "subscribe", "subscription": subscription}))
                early = await _confirm_subscriptions(ws, len(symbols))
            except (websockets.ConnectionClosed, TimeoutError) as exc:
                # The server drops the whole connection on a coin it doesn't list.
                raise FeedDisconnected(
                    f"subscribing to {len(symbols)} symbols failed — is one not listed? {exc!r}"
                ) from exc
            yield _messages(ws, early)
        finally:
            await ws.close()


async def _confirm_subscriptions(ws, count: int) -> list[CandleMessage]:
    """Wait until Hyperliquid has confirmed all `count` subscriptions.

    Only then is a REST gap-fill safe to start (see `MarketFeed.connect`). Candles that
    arrive in the meantime are kept, not lost.
    """
    early: list[CandleMessage] = []
    confirmed = 0
    async with asyncio.timeout(WS_SUBSCRIBE_TIMEOUT_SECONDS):
        while confirmed < count:
            message = json.loads(await ws.recv())
            if message.get("channel") == "subscriptionResponse":
                confirmed += 1
            elif (candle := _candle_message(message)) is not None:
                early.append(candle)
    return early


async def _messages(ws, early: list[CandleMessage]) -> AsyncIterator[CandleMessage]:
    for message in early:
        yield message
    last_ping = time.monotonic()
    try:
        while True:
            # Ping on a timer whether or not data is flowing: the server drops a
            # connection that hasn't *sent* it anything for 60 s. recv() is safe to cancel.
            until_ping = max(WS_PING_SECONDS - (time.monotonic() - last_ping), 0)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=until_ping)
            except TimeoutError:
                raw = None
            if time.monotonic() - last_ping >= WS_PING_SECONDS:
                await ws.send(PING)
                last_ping = time.monotonic()
            if raw is not None and (candle := _candle_message(json.loads(raw))) is not None:
                yield candle
    except websockets.ConnectionClosed as exc:
        raise FeedDisconnected(f"stream closed: {exc!r}") from exc


def _candle_message(message: dict) -> CandleMessage | None:
    """A candle update as `(symbol, candle)`. None for pongs and confirmations."""
    channel = message.get("channel")
    if channel == "candle":
        data = message["data"]
        return data["s"], _candle(data)
    if channel == "error":
        raise FeedError(f"websocket error: {message.get('data')}")
    return None


def _candle(raw: dict) -> Candle:
    return Candle(
        open_time=int(raw["t"]),
        open=float(raw["o"]),
        high=float(raw["h"]),
        low=float(raw["l"]),
        close=float(raw["c"]),
        volume=float(raw["v"]),
    )


def _tradeable(market: dict, context: dict) -> bool:
    """Listed, enabled, and liquid enough for MACD to mean something."""
    if market.get("isDelisted"):
        return False
    # HIP-3 markets carry growthMode; anything but "enabled" is paused or withdrawn.
    if market.get("growthMode", "enabled") != "enabled":
        return False
    try:
        day_volume = float(context["dayNtlVlm"])
        open_interest = float(context["openInterest"]) * float(context["markPx"])
    except (KeyError, TypeError, ValueError):
        # A market with missing or malformed figures can't be judged liquid. Leaving it
        # out is deliberate, and logged so it is visible.
        log.warning("skipping %s: unreadable asset context", market.get("name"))
        return False
    return day_volume >= MIN_DAY_VOLUME_USD and open_interest >= MIN_OPEN_INTEREST_USD
