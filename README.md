# live-macd-searcher

One long-running process that watches Hyperliquid perps — core crypto plus the HIP-3
`xyz` markets — on the **1-hour timeframe** and reports **contraction windows**: runs of
closed bars where the MACD histogram is shrinking toward zero without changing sign.

Each window is scored by where the MACD and signal lines sit relative to zero and where
price sits relative to the Bollinger middle band. It is then followed through the cross
until it resolves, so the score can be checked against what actually happened. Results
are shown on a private web page, reached over Tailscale.

This is a **measuring instrument, not a trading bot.** It places no orders, and nothing
it shows is financial advice.

## Status

Early build. Progress and build order: [docs/PLAN.md](docs/PLAN.md).

## Development

Requires [uv](https://docs.astral.sh/uv/), which also manages the Python version.

```bash
uv sync              # create .venv and install dependencies
uv run pytest -q     # run the test suite
uv run ruff check    # lint
```

Run it — detector and web page in one process, on http://127.0.0.1:8001:

```bash
npm --prefix ui ci && npm --prefix ui run build   # the page, once (and after UI changes)
uv run live-macd-searcher                         # --db, --host, --port to override
```

A cold start warms the whole universe over REST (~7 minutes, paced) before the board
fills. To work on the page with hot reload, run the app as above and, alongside it,
`npm --prefix ui run dev` (http://localhost:5173, proxying `/api` to the app).
Deployment is milestone M9.

## Docs

- [docs/DESIGN.md](docs/DESIGN.md) — what it does, how, and what was deliberately left out
- [docs/PLAN.md](docs/PLAN.md) — milestones and the decisions log
- [CLAUDE.md](CLAUDE.md) — vocabulary, invariants, and how the code is written
