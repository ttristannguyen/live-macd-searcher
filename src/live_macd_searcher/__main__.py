"""Run the app — detector and web — in one process:
`live-macd-searcher [--db PATH] [--host 127.0.0.1] [--port 8001]`.

Bound to localhost by default; it is reached over Tailscale, never exposed (DESIGN §13).
"""

import argparse
import logging
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI

from .detect.config import REST_WEIGHT_PER_MIN
from .ingest.hyperliquid import HyperliquidFeed
from .ingest.pacer import RestPacer
from .runtime import Runtime
from .store.db import connect
from .web.app import create_app
from .web.stream import Broadcaster

DEFAULT_DB = Path("state/live_macd_searcher.sqlite3")


def production_app(db_path: Path) -> FastAPI:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    client = httpx.AsyncClient()
    broadcaster = Broadcaster()
    runtime = Runtime(
        connect(db_path),
        HyperliquidFeed(client, RestPacer(REST_WEIGHT_PER_MIN)),
        on_window=broadcaster.window,
        on_provisional=broadcaster.provisional,
    )

    async def run() -> None:
        try:
            await runtime.run()
        finally:
            await client.aclose()

    return create_app(db_path, runtime.status, run=run, broadcaster=broadcaster)


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"default: {DEFAULT_DB}")
    parser.add_argument("--host", default="127.0.0.1", help="default: localhost only")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs every request at INFO; at hundreds per warm-up that drowns the app's own.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(production_app(args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    cli()
