"""Run the detector headless: `python -m live_macd_searcher [--db PATH]`.

The web app (M6) will run this same runtime inside uvicorn; until then, this is how it
runs on its own — for a local soak, or to fill a database.
"""

import argparse
import asyncio
import logging
from pathlib import Path

import httpx

from .detect.config import REST_WEIGHT_PER_MIN
from .ingest.hyperliquid import HyperliquidFeed
from .ingest.pacer import RestPacer
from .runtime import Runtime
from .store.db import connect

DEFAULT_DB = Path("state/live_macd_searcher.sqlite3")


async def main(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        async with httpx.AsyncClient() as client:
            feed = HyperliquidFeed(client, RestPacer(REST_WEIGHT_PER_MIN))
            await Runtime(conn, feed).run()
    finally:
        conn.close()


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"default: {DEFAULT_DB}")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs every request at INFO; at hundreds per warm-up that drowns the app's own.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main(args.db))
    except KeyboardInterrupt:
        pass  # Ctrl-C is how it stops; every bar is already stored atomically


if __name__ == "__main__":
    cli()
