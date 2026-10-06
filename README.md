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

## Deployment (the droplet, beside `macd_searcher`)

It runs on the same DigitalOcean droplet as `macd_searcher`, as your normal user, and
copies its setup on purpose (DESIGN §13): `uv`, a systemd unit, localhost only, reached
over Tailscale. It shares nothing with `macd_searcher` but the machine.

### 1. Before changing anything

```bash
free -h                  # 1 GB droplet: note memory and whether swap exists
ss -ltnp | grep 8001     # must print nothing: the port is free
tailscale serve status   # note it: macd_searcher's handler must look the same afterwards
node -v                  # the page builds with Node 18+; see step 3 if missing or older
```

### 2. Clone and install

```bash
cd ~
git clone https://github.com/ttristannguyen/live-macd-searcher.git
cd live-macd-searcher
uv sync
.venv/bin/python scripts/smoke_hyperliquid.py   # 30 s of live candles: the feed works from here
```

Don't run the smoke test's `--warm` on the droplet: the app does that itself, paced.

### 3. Build the page

```bash
npm --prefix ui ci && npm --prefix ui run build
```

On 1 GB this is the step most likely to run out of memory. If it gets killed, build on
your desktop instead and copy it up:

```bash
# on the desktop
npm --prefix ui ci && npm --prefix ui run build
scp -r ui/dist <droplet>:~/live-macd-searcher/ui/
```

### 4. Run it under systemd

```bash
sudo cp deploy/live-macd-searcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now live-macd-searcher
journalctl -u live-macd-searcher -f      # watch the cold start: ~7 minutes, paced
curl -s http://127.0.0.1:8001/api/health  # "starting", then "ok" once warmed
```

### 5. Reach it over Tailscale

```bash
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8001
tailscale serve status   # the new :8443 handler, and macd_searcher's 443 unchanged
```

Then open `https://<droplet>.<tailnet>.ts.net:8443/`. It is tailnet-only: from your
desktop, `curl --max-time 5 http://<droplet-public-ip>:8001` must fail to connect.

### Updating

```bash
cd ~/live-macd-searcher && git pull && uv sync && npm --prefix ui ci && npm --prefix ui run build && sudo systemctl restart live-macd-searcher
```

A restart is safe: every write is atomic, and boot rebuilds state exactly from stored
bars. Within the hour it streams again in seconds.

### Keeping an eye on it

- **Health:** the page's status pill, or `curl -s http://127.0.0.1:8001/api/health`.
  Every change of health is logged: `journalctl -u live-macd-searcher | grep health`.
- **Backups:** from the desktop, `.\scripts\sync_prod_db.ps1 -VmHost <droplet>` pulls a
  consistent snapshot to `state\prod_snapshot.sqlite3` and reports its freshness.
- **Bar audit (once, after a day):** `.venv/bin/python scripts/audit_bars.py` checks a
  day of stored bars against REST, paced at half the app's budget.
- **`macd_searcher` unaffected:** its REST retries per day should not rise after this
  app arrives —
  `grep "attempt .* failed" ~/macd_searcher/logs/scan.log | cut -c1-10 | sort | uniq -c`.

## Docs

- [docs/DESIGN.md](docs/DESIGN.md) — what it does, how, and what was deliberately left out
- [docs/PLAN.md](docs/PLAN.md) — milestones and the decisions log
- [CLAUDE.md](CLAUDE.md) — vocabulary, invariants, and how the code is written
