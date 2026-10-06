"""The live stream (PLAN M7): events reach every board, a slow or vanished browser is
dropped rather than waited on, and the detector never notices either way."""

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from live_macd_searcher.detect.config import SSE_CLIENT_BACKLOG
from live_macd_searcher.detect.symbol_state import Provisional
from live_macd_searcher.detect.window import Window, WindowEvent
from live_macd_searcher.runtime import Runtime, RuntimeStatus
from live_macd_searcher.store.db import connect
from live_macd_searcher.web.app import create_app
from live_macd_searcher.web.stream import Broadcaster, event_stream
from tests.fakes import FakeFeed, Session, stream_hours, synthetic_history

WINDOW = Window(
    symbol="BTC", asset_class="crypto", side="bullish", state="active", started_at=0,
    peak_pct=-0.5, price_at_open=100.0, bars=2, regime="reversal", line_turn=True,
    strength=50.0, updated_at=3_600_000, hist_pct=-0.3, macd_pct=-1.0, signal_pct=-0.7,
    band="near", band_offset=0.0,
)  # fmt: skip


# A regression here tends to make a stream wait forever. Bound every wait, so the test
# fails in seconds instead of hanging the suite.
WAIT = 2.0


async def read(stream, count: int) -> list[str]:
    return [await asyncio.wait_for(anext(stream), WAIT) for _ in range(count)]


async def read_to_end(stream) -> list[str]:
    async def collect():
        return [chunk async for chunk in stream]

    return await asyncio.wait_for(collect(), WAIT)


# --- the broadcaster ---------------------------------------------------------------


def test_events_reach_every_subscriber_in_order_as_sse():
    async def go():
        broadcaster = Broadcaster()
        streams = [event_stream(broadcaster, broadcaster.subscribe()) for _ in range(2)]
        broadcaster.window(WindowEvent("opened", WINDOW), window_id=7)
        broadcaster.provisional([Provisional("BTC", 0, 100.0, -0.1, -1.0, -0.9, 0.05)])
        return [await read(stream, 3) for stream in streams]

    for connected, opened, tick in asyncio.run(go()):
        assert connected == ": connected\n\n"
        name, data = opened.removesuffix("\n\n").split("\n")
        assert name == "event: window.opened"
        payload = json.loads(data.removeprefix("data: "))
        assert (payload["id"], payload["symbol"], payload["state"]) == (7, "BTC", "active")
        assert tick.startswith("event: tick.provisional\ndata: ")
        assert json.loads(tick.split("data: ")[1])["readings"][0]["symbol"] == "BTC"


def test_a_quiet_stream_sends_keep_alives():
    async def go():
        broadcaster = Broadcaster()
        stream = event_stream(broadcaster, broadcaster.subscribe(), heartbeat=0.01)
        return await read(stream, 3)

    assert asyncio.run(go())[1:] == [": keep-alive\n\n"] * 2


def test_a_stalled_client_is_dropped_and_never_blocks_publishing():
    async def go():
        broadcaster = Broadcaster()
        stalled = broadcaster.subscribe()  # never reads
        healthy = broadcaster.subscribe()
        healthy_stream = event_stream(broadcaster, healthy)
        await anext(healthy_stream)  # ": connected"
        for n in range(SSE_CLIENT_BACKLOG + 50):
            broadcaster.publish("window.updated", str(n))  # returns at once, every time
            if n % 100 == 0:  # the healthy client keeps up
                while not healthy.queue.empty():
                    await anext(healthy_stream)
        # Checked here, while the healthy client is still connected: leaving
        # asyncio.run closes its stream, which (rightly) unsubscribes it.
        healthy_still_subscribed = healthy in broadcaster.subscribers
        stalled_stream = event_stream(broadcaster, stalled)
        stalled_output = await read_to_end(stalled_stream)
        return broadcaster, stalled, healthy, healthy_still_subscribed, stalled_output

    broadcaster, stalled, healthy, healthy_still_subscribed, stalled_output = asyncio.run(go())
    assert stalled.dropped and stalled not in broadcaster.subscribers
    assert not healthy.dropped and healthy_still_subscribed
    # The dropped client's stream ends, so its browser reconnects and refetches.
    assert stalled_output == [": connected\n\n"]


def test_closing_a_stream_unsubscribes_it():
    async def go():
        broadcaster = Broadcaster()
        stream = event_stream(broadcaster, broadcaster.subscribe())
        await anext(stream)
        await stream.aclose()
        return broadcaster

    assert asyncio.run(go()).subscribers == set()


# --- the runtime publishes only what it has stored ----------------------------------


def test_the_runtime_announces_stored_windows_with_their_row_ids(tmp_path):
    history = {"BTC": synthetic_history(700, 0.0)}
    feed = FakeFeed(history, [Session(now_hour=450, messages=stream_hours(history, 450, 699))])
    heard: list[tuple[WindowEvent, int, tuple]] = []
    conn = connect(tmp_path / "db.sqlite3")

    def on_window(event: WindowEvent, window_id: int) -> None:
        stored = conn.execute("SELECT state, updated_at FROM windows WHERE id = ?",
                              (window_id,)).fetchone()  # fmt: skip
        heard.append((event, window_id, tuple(stored)))

    runtime = Runtime(conn, feed, now_ms=feed.now_ms, on_window=on_window)

    async def go():
        await runtime.boot()
        with pytest.raises(ConnectionError):
            await runtime.ingest.run_session()

    asyncio.run(go())
    assert {event.kind for event, _, _ in heard} >= {"opened", "updated", "crossed", "resolved"}
    for event, _, stored in heard:
        # Already in the database, exactly as announced, when the stream hears it.
        assert stored == (event.window.state, event.window.updated_at)


# --- a real server: a browser hangs up mid-stream ------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_client_hanging_up_mid_stream_leaves_the_detector_running(tmp_path):
    broadcaster = Broadcaster()
    published = {"count": 0}

    async def detector():
        # Stands in for the runtime: publishes an event every few milliseconds, forever.
        while True:
            broadcaster.window(WindowEvent("updated", WINDOW), window_id=1)
            published["count"] += 1
            await asyncio.sleep(0.005)

    status = RuntimeStatus(True, True, True, None, None, 1, 1, 0, 0)
    app = create_app(tmp_path / "db.sqlite3", lambda: status, run=detector,
                     broadcaster=broadcaster, on_failure=lambda exc: None)  # fmt: skip
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            time.sleep(0.01)
        with httpx.stream("GET", f"http://127.0.0.1:{port}/api/stream", timeout=5) as response:
            events = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    events.append(line)
                if len(events) == 3:
                    break  # hang up mid-stream
        assert events == ["event: window.updated"] * 3

        before = published["count"]
        time.sleep(0.3)
        assert published["count"] > before + 10  # the detector never noticed
        deadline = time.monotonic() + 5
        while broadcaster.subscribers and time.monotonic() < deadline:
            time.sleep(0.05)
        assert broadcaster.subscribers == set()  # the server let the client go
        assert httpx.get(f"http://127.0.0.1:{port}/api/health").json()["status"] != "failed"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
