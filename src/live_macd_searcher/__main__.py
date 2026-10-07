"""Run the app — detector and web — in one process:
`live-macd-searcher [--db PATH] [--host 127.0.0.1] [--port 8001]`.

Bound to localhost by default; it is reached over Tailscale, never exposed (DESIGN §13).
"""

import argparse
import logging
import subprocess
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


def code_version() -> str:
    """The git commit running, for each run's provenance row (PLAN D-2) — with "+dirty"
    if the checkout has local edits, so an edited copy never passes for a release."""
    here = Path(__file__).parent

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=here, capture_output=True, text=True,
                              check=True).stdout.strip()  # fmt: skip

    try:
        commit = git("rev-parse", "--short=12", "HEAD")
        return commit + ("+dirty" if git("status", "--porcelain", "--untracked-files=no") else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"  # not a git checkout: say so rather than guess


def production_app(db_path: Path) -> FastAPI:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    client = httpx.AsyncClient()
    broadcaster = Broadcaster()
    runtime = Runtime(
        connect(db_path),
        HyperliquidFeed(client, RestPacer(REST_WEIGHT_PER_MIN)),
        on_window=broadcaster.window,
        on_provisional=broadcaster.provisional,
        code_version=code_version(),
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
