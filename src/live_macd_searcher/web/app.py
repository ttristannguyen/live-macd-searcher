"""The FastAPI app: a read-only JSON API under /api, the built UI at /, and the detector
running inside the same process (DESIGN §5).

Private by design (CLAUDE.md guardrails): bound to 127.0.0.1 and reached over Tailscale.
Every request reads through its own read-only SQLite connection, so no endpoint *can*
write; the detector is the database's only writer.
"""

import asyncio
import logging
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from ..detect.config import BAR_RETENTION_DAYS
from ..detect.vocabulary import AssetClass, Band, Regime, Side, State
from ..runtime import RuntimeStatus
from ..store.db import connect
from . import queries
from .health import judge
from .models import Board, Health, Series, WindowDetail, WindowOut
from .series import indicator_series

log = logging.getLogger(__name__)

UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"
MAX_SERIES_BARS = BAR_RETENTION_DAYS * 24  # everything retention keeps


def _stop_process(exc: BaseException) -> None:
    # A web page over a dead detector would serve frozen windows as if live. Exit hard
    # and non-zero so systemd restarts us. Safe: every write is already atomic (M5).
    os._exit(1)


def create_app(
    db_path: Path,
    status: Callable[[], RuntimeStatus],
    *,
    run: Callable[[], Awaitable[None]] | None = None,
    on_failure: Callable[[BaseException], None] = _stop_process,
) -> FastAPI:
    """`run` is the detector, started with the app and stopped with it. Tests leave it out
    and serve a prepared database."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        connect(db_path).close()  # the schema exists before the first read-only request
        task = asyncio.create_task(run()) if run is not None else None
        if task is not None:
            task.add_done_callback(watch)
        yield
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def watch(task: asyncio.Task) -> None:
        if task.cancelled():
            return  # shutting down
        exc = task.exception() or RuntimeError("the detector exited")
        app.state.failure = repr(exc)
        log.critical("the detector stopped", exc_info=exc)
        on_failure(exc)

    app = FastAPI(title="live-macd-searcher", lifespan=lifespan)
    app.state.db_uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    app.state.status = status
    app.state.failure = None

    def health_now(conn: sqlite3.Connection) -> Health:
        return judge(app.state.status(), app.state.failure,
                     queries.newest_bar_time(conn), time.time())  # fmt: skip

    @app.get("/api/windows", response_model=Board)
    def windows(
        conn: Annotated[sqlite3.Connection, Depends(read_only)],
        state: Annotated[list[State], Query()] = ["active", "crossed"],  # noqa: B006
        asset_class: Annotated[list[AssetClass] | None, Query()] = None,
        side: Annotated[list[Side] | None, Query()] = None,
        regime: Annotated[list[Regime] | None, Query()] = None,
        band: Annotated[list[Band] | None, Query()] = None,
        min_strength: Annotated[float, Query(ge=0, le=100)] = 0,
        min_bars: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> Board:
        rows = queries.board(
            conn, states=state, asset_classes=asset_class, sides=side, regimes=regime,
            bands=band, min_strength=min_strength, min_bars=min_bars, limit=limit,
        )  # fmt: skip
        return Board(
            status=health_now(conn).status,
            as_of=queries.newest_bar_time(conn),
            windows=[WindowOut(**dict(row)) for row in rows],
        )

    @app.get("/api/windows/{window_id}", response_model=WindowDetail)
    def window(
        window_id: int, conn: Annotated[sqlite3.Connection, Depends(read_only)]
    ) -> WindowDetail:
        row = queries.window(conn, window_id)
        if row is None:
            raise HTTPException(404, f"no window {window_id}")
        found = WindowOut(**dict(row))
        series = indicator_series(queries.bars(conn, found.symbol))
        trace = [b for b in series if found.started_at <= b.open_time <= found.updated_at]
        return WindowDetail(
            window=found,
            trace=trace,
            # Every bar from the peak to the latest applied, or retention took some.
            trace_complete=bool(trace) and trace[0].open_time == found.started_at,
        )

    @app.get("/api/symbols/{symbol}/series", response_model=Series)
    def series(
        symbol: str,
        conn: Annotated[sqlite3.Connection, Depends(read_only)],
        bars: Annotated[int, Query(ge=1, le=MAX_SERIES_BARS)] = 200,
    ) -> Series:
        stored = queries.bars(conn, symbol)
        if not stored:
            raise HTTPException(404, f"no bars stored for {symbol}")
        return Series(symbol=symbol, bars=indicator_series(stored)[-bars:])

    @app.get("/api/health", response_model=Health)
    def health(conn: Annotated[sqlite3.Connection, Depends(read_only)]) -> Health:
        return health_now(conn)

    if UI_DIST.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")

    return app


def read_only(request: Request) -> Iterator[sqlite3.Connection]:
    """A per-request connection that cannot write: the guardrail, enforced by SQLite."""
    conn = sqlite3.connect(request.app.state.db_uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
