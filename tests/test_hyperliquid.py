"""The Hyperliquid client against a mocked HTTP transport, and websocket message parsing.

Payload shapes are as observed on the live API (2026-10-05): prices and volumes arrive
as strings. The live websocket itself is exercised by scripts/smoke_hyperliquid.py.
"""

import asyncio
import json

import httpx
import pytest

from live_macd_searcher.ingest.feed import FeedError
from live_macd_searcher.ingest.hyperliquid import HyperliquidFeed, _candle_message
from live_macd_searcher.market import Candle

HOUR = 3_600_000


class RecordingPacer:
    def __init__(self) -> None:
        self.spent: list[int] = []

    async def spend(self, weight: int) -> None:
        self.spent.append(weight)


async def no_sleep(seconds: float) -> None:
    pass


def feed_answering(handler):
    pacer = RecordingPacer()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HyperliquidFeed(client, pacer, sleep=no_sleep), pacer


def raw_candle(hour: int, close: str = "101.5") -> dict:
    return {"t": hour * HOUR, "T": (hour + 1) * HOUR - 1, "s": "BTC", "i": "1h",
            "o": "100.0", "c": close, "h": "102.0", "l": "99.0", "v": "12.5", "n": 40}  # fmt: skip


def liquid(mark="10.0"):
    return {"dayNtlVlm": "5000000.0", "openInterest": "1000000.0", "markPx": mark}


def test_universe_keeps_listed_enabled_liquid_markets_across_core_and_xyz():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("dex") == "xyz":
            markets = [{"name": "xyz:TSLA", "growthMode": "enabled"},
                       {"name": "xyz:PAUSED", "growthMode": "reduceOnly"}]  # fmt: skip
            contexts = [liquid(), liquid()]
        else:
            markets = [{"name": "BTC"}, {"name": "DEAD", "isDelisted": True},
                       {"name": "THIN"}, {"name": "JUNK"}]  # fmt: skip
            thin = {"dayNtlVlm": "1000.0", "openInterest": "1.0", "markPx": "1.0"}
            contexts = [liquid(), liquid(), thin, liquid(mark=None)]
        return httpx.Response(200, json=[{"universe": markets}, contexts])

    feed, pacer = feed_answering(handler)
    universe = asyncio.run(feed.universe())
    assert universe.tradeable == ["BTC", "xyz:TSLA"]
    # Paused and thin markets are still listed, so still safe to subscribe to; a
    # delisted one is not.
    assert universe.listed == {"BTC", "THIN", "JUNK", "xyz:TSLA", "xyz:PAUSED"}
    assert pacer.spent == [20, 20]


def test_candles_parse_strings_sorted_oldest_first_and_pay_for_their_size():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[raw_candle(11), raw_candle(10, close="100.25")])

    feed, pacer = feed_answering(handler)
    candles = asyncio.run(feed.candles("BTC", 10 * HOUR, 410 * HOUR))
    assert candles == [
        Candle(10 * HOUR, 100.0, 102.0, 99.0, 100.25, 12.5),
        Candle(11 * HOUR, 100.0, 102.0, 99.0, 101.5, 12.5),
    ]
    assert pacer.spent == [20 + 7]  # 401 candles requested: ceil(401 / 60) = 7 extra


def test_a_429_is_retried_paced_and_counted():
    answers = iter([httpx.Response(429, text="slow down"), httpx.Response(200, json=[])])
    feed, pacer = feed_answering(lambda request: next(answers))
    assert asyncio.run(feed.candles("BTC", 0, 0)) == []
    assert feed.rate_limited == 1
    assert len(pacer.spent) == 2  # the retry paid its own way through the pacer


def test_a_network_error_is_retried():
    answers = iter([httpx.ConnectError("down"), httpx.Response(200, json=[])])

    def handler(request):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    feed, _ = feed_answering(handler)
    assert asyncio.run(feed.candles("BTC", 0, 0)) == []


def test_persistent_server_errors_give_up_after_three_attempts():
    feed, pacer = feed_answering(lambda request: httpx.Response(503))
    with pytest.raises(FeedError, match="after 3 attempts"):
        asyncio.run(feed.candles("BTC", 0, 0))
    assert len(pacer.spent) == 3


def test_a_bad_request_is_not_retried():
    feed, pacer = feed_answering(lambda request: httpx.Response(400, text="bad coin"))
    with pytest.raises(FeedError, match="HTTP 400"):
        asyncio.run(feed.candles("NOPE", 0, 0))
    assert len(pacer.spent) == 1


# --- websocket messages ------------------------------------------------------------


def test_a_candle_message_parses_its_string_prices():
    message = {"channel": "candle", "data": raw_candle(5)}
    assert _candle_message(message) == ("BTC", Candle(5 * HOUR, 100.0, 102.0, 99.0, 101.5, 12.5))


@pytest.mark.parametrize(
    "message",
    [{"channel": "pong"}, {"channel": "subscriptionResponse", "data": {"method": "subscribe"}}],
)
def test_pongs_and_confirmations_are_not_candles(message):
    assert _candle_message(message) is None


def test_an_error_message_is_a_feed_error():
    with pytest.raises(FeedError, match="websocket error"):
        _candle_message({"channel": "error", "data": "Invalid subscription"})
