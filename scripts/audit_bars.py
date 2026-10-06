"""One-off audit (PLAN M9, DESIGN §11): do stored bars match Hyperliquid's candleSnapshot?

Closed bars come from the last websocket snapshot before the next hour arrives. This
checks that assumption against REST for every stored symbol over the last N hours:

    uv run python scripts/audit_bars.py [--db PATH] [--hours 24] [--weight-per-min 300]

Reads the database read-only, so it is safe beside the running app. Its REST spending is
paced, at half the app's own budget by default: on the droplet it shares the IP with the
app and with macd_searcher. If closed bars ever disagree, DESIGN §11 says closed bars
move to REST confirmation.
"""

import argparse
import asyncio
import sqlite3
from collections import Counter
from pathlib import Path

import httpx

from live_macd_searcher.ingest.feed import HOUR_MS
from live_macd_searcher.ingest.hyperliquid import HyperliquidFeed
from live_macd_searcher.ingest.pacer import RestPacer
from live_macd_searcher.market import Candle

FIELDS = ("open", "high", "low", "close", "volume")
AGREES_NO_TRADES = "filled, REST agrees: no trades"  # no candle, or an empty one
FILLED_BUT_TRADED = "FILLED BUT REST HAS TRADES"


def stored_bars(db: Path, hours: int) -> dict[str, list[Candle]]:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    newest = conn.execute("SELECT MAX(open_time) FROM bars").fetchone()[0]
    since = newest - (hours - 1) * HOUR_MS
    by_symbol: dict[str, list[Candle]] = {}
    for symbol, *values in conn.execute(
        "SELECT symbol, open_time, open, high, low, close, volume FROM bars"
        " WHERE open_time >= ? ORDER BY symbol, open_time",
        (since,),
    ):
        by_symbol.setdefault(symbol, []).append(Candle(*values))
    conn.close()
    return by_symbol


def compare(stored: list[Candle], fetched: list[Candle]) -> tuple[Counter, list[str]]:
    rest = {c.open_time: c for c in fetched}
    verdicts, details = Counter(), []
    for bar in stored:
        truth = rest.get(bar.open_time)
        # An hour we filled because no candle arrived (PLAN D13). REST either has no
        # candle for it, or — seen on xyz markets — an explicit zero-volume flat one.
        if bar.volume == 0 and (truth is None or truth == bar):
            verdicts[AGREES_NO_TRADES] += 1
        elif bar.volume == 0:
            verdicts[FILLED_BUT_TRADED] += 1
            details.append(f"  {bar.open_time}: filled flat, REST has {truth}")
        elif truth is None:
            verdicts["STORED, NOT IN REST"] += 1
        elif all(getattr(bar, f) == getattr(truth, f) for f in FIELDS):
            verdicts["match"] += 1
        else:
            verdicts["DIFFERS"] += 1
            diffs = ", ".join(f"{f} {getattr(bar, f)} vs {getattr(truth, f)}"
                              for f in FIELDS if getattr(bar, f) != getattr(truth, f))  # fmt: skip
            details.append(f"  {bar.open_time}: {diffs}")
    return verdicts, details


async def main(db: Path, hours: int, weight_per_min: int) -> None:
    bars = stored_bars(db, hours)
    print(f"auditing the last {hours} stored hours of {len(bars)} symbols, "
          f"paced at {weight_per_min} weight/min")  # fmt: skip
    totals: Counter = Counter()
    async with httpx.AsyncClient() as client:
        feed = HyperliquidFeed(client, RestPacer(weight_per_min))
        for symbol, stored in bars.items():
            fetched = await feed.candles(symbol, stored[0].open_time, stored[-1].open_time)
            verdicts, details = compare(stored, fetched)
            totals += verdicts
            if details:
                print(symbol, dict(verdicts), *details[:5], sep="\n")
    print("\ntotals:", dict(totals))
    print("REST 429s:", feed.rate_limited)
    bad = sum(n for verdict, n in totals.items() if verdict not in ("match", AGREES_NO_TRADES))
    print("VERDICT:", "stored bars match REST" if bad == 0 else f"{bad} disagree: see DESIGN §11")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=Path("state/live_macd_searcher.sqlite3"))
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--weight-per-min", type=int, default=300)
    args = parser.parse_args()
    asyncio.run(main(args.db, args.hours, args.weight_per_min))
