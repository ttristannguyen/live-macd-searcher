## What this is

`live-macd-searcher` is one long-running process that watches Hyperliquid perps
(core crypto plus the HIP-3 `xyz` markets) on the **1-hour timeframe**, refreshing
every 10 minutes, and continuously reports
**contraction windows** — runs of closed bars where the MACD histogram is shrinking
toward zero without changing sign. It scores each window using where the MACD and
signal lines sit relative to the zero line and where price sits relative to the
Bollinger middle band. It follows every window through the cross to resolution, so
the scoring can be checked against what actually happened.

Full design: [docs/DESIGN.md](docs/DESIGN.md). Read it before adding anything
structural — it records what we deliberately left out, and why.
Build order and current progress: [docs/PLAN.md](docs/PLAN.md).

It runs on the DigitalOcean droplet beside `macd_searcher` (`~/macd_searcher`), bound to
localhost and reached only over Tailscale. Deployment choices copy that project's on
purpose — see DESIGN §13.

## North star

Write code that is **clean, readable, and maintainable** — optimised for the next
person to read it (usually future us), not for cleverness or for problems we don't
have yet. When in doubt, choose the simpler option and the clearer name.

This app is a measuring instrument. A signal that is subtly wrong is worse than no
signal, because you will act on it. Correctness of the numbers and honesty about their
provenance outrank every other consideration here, including elegance.

## Vocabulary

Use these words and only these words, in code, in comments, in the API, and in the UI.
Consistent names are the cheapest documentation we get.

| Term | Meaning |
| --- | --- |
| **window** | A maximal run of closed bars where the histogram holds its sign and `abs(hist)` strictly shrinks. Once published, it is followed through the cross until it resolves. |
| **peak** | The bar where `abs(hist)` was at its extreme — the window's origin, not part of the run. |
| **bars** | Number of shrink steps since the peak. |
| **side** | `bullish` (negative histogram shrinking) or `bearish` (positive histogram shrinking). |
| **regime** | `reversal`, `continuation`, or `transition` — from where MACD and signal sit relative to zero. |
| **band** | `far`, `near`, or `through` — where close sits relative to the Bollinger middle band, read in the window's direction. |
| **state** | `active` or `crossed` (live), then `failed`, `hit`, `reversed`, or `expired` (terminal). `crossed` always means the *histogram* changed sign. |
| **target** | The outer Bollinger band on the far side. Touching it after the cross resolves the window as `hit`. |
| **refresh** | The 10-minute recomputation of `provisional` readings from the forming bar. Never moves a window. |
| **asset class** | `crypto`, `equity`, `index`, `commodity`, or `fx` — from the symbol, as in `macd_searcher`. |
| **strength** | 0–100 score from `detect/strength.py`. Opinionated, tunable, in one place. |
| **warming** | A symbol without enough history for its EMAs to have converged. Produces nothing. |
| **provisional** | A reading from the still-forming bar. Displayed, never persisted, never acted on. |

Never write "signal" to mean a detected window — `signal` is the MACD signal line and
nothing else. Likewise never write "crossed" for price passing the middle band — that
is `through`. `crossed` is the histogram changing sign and nothing else.

## Invariants

These are not preferences. Breaking one is a bug even if every test passes.

1. **Signals fire on closed bars only.** The forming bar may be computed and shown as
   `provisional`, but it never opens, extends, crosses, or resolves a window. This is
   what stops the board repainting. Refreshes between hourly closes update provisional
   readings and nothing else. A candle is closed when the exchange has sent a later one,
   not when our clock says the hour is up.
2. **Exchange time is the only clock.** Bar logic keys off the candle's `open_time`.
   `datetime.now()` belongs in refresh scheduling, REST pacing, and health reporting and
   nowhere else.
3. **Normalise before comparing.** Anything ranked, thresholded, or shown beside another
   symbol is a percentage of close, or (for Bollinger position) in band widths. Raw
   price-unit values never cross symbol boundaries.
4. **Nothing is emitted while warming.** A half-converged EMA produces plausible-looking
   garbage. Silence is the correct output.
5. **Every window is followed to resolution, through the cross.** `failed`, `hit`,
   `reversed`, and `expired` all get written, with excursions. The outcome record is
   the only reason to trust the score.
6. **The detector is pure.** `indicators/` and `detect/` import no DB, no clock, no web
   framework. If you need to reach for one, the logic is in the wrong layer.

## Principles we follow

In rough priority order. Each is anchored to a concrete decision in this codebase so we
apply them consistently.

- **KISS — keep it simple.** The simplest thing that solves *today's* problem. *Example:*
  About 140 symbols on hourly bars is one websocket connection and about 140 closed bars
  an hour, so there is no queue, no worker pool, and no message bus — one asyncio loop,
  a timer, and a `dict[str, SymbolState]`. The whole app is one `uvicorn` invocation.

- **YAGNI — you aren't gonna need it.** Don't add parameters, flags, or abstraction for a
  hypothetical future. Add them when the need is real. *Example:* "shrinking" means
  *strictly* shrinking. There is no tolerance knob for a one-bar hiccup, because we have
  not yet seen one cost us a good window — and if we do, it becomes one constant, not a
  config system.

- **DRY, with judgment.** One source of truth for a fact, but a little duplication beats
  the wrong abstraction. *Example:* `MIN_RUN_BARS`, `WEIGHTS`, and `BACKFILL_BARS` are
  defined exactly once in `detect/config.py`; meanwhile each query in `web/queries.py`
  restates its own `SELECT` rather than being forced through one rigid builder.

- **Separation of concerns / layering.** Keep pure logic, I/O, and transport in different
  places. *Example:* `indicators/` and `detect/` are pure (numbers in, numbers out) →
  `ingest/` owns sockets and REST → `store/` owns all SQL → `web/models.py` owns the
  response shapes → `web/app.py` owns the routes. The frontend mirrors this: `api/`
  (data) → `components/` (presentation) → `pages/` (composition).

- **Readability over cleverness.** Match the surrounding style, comment density, and
  naming. Clear names beat short ones. If a line needs a second read to parse, simplify
  it or explain it. *Example:* `bars_to_cross` rather than `btc` — and yes, that is
  exactly why.

- **Validate at the boundary.** Make illegal input impossible to express, and let it fail
  fast and loudly at the edge. *Example:* `side`, `regime`, and `state` are `Literal`
  types, so FastAPI rejects anything else with a 422 before it reaches our SQL — no
  hand-written guards needed.

- **Comments explain *why*, not *what*.** The code says what it does; comments capture
  the reasoning, the trade-off, or the provenance. *Example:* the comment on
  `BACKFILL_BARS = 400` records the EMA convergence maths that produced the number, not
  the fact that it is a backfill count.

- **Tests pin behaviour, and run green.** Deterministic, fast, asserting observable
  behaviour rather than internals. They are the safety net that lets us re-tune scoring
  with confidence. *Example:* golden MACD vectors to 1e-9, hand-written histogram
  sequences driving the detector state machine, and a scripted `FakeFeed` for end-to-end
  runs with no network. Optional deps are guarded (`pytest.importorskip`) so the core
  suite stays green without them.

- **Fail honestly.** Surface errors; don't paper over them. A stale feed shows as stale
  in `/api/health`; a symbol that cannot warm up stays `warming` rather than being
  quietly scored. Where we *choose* to swallow a failure it is deliberate and written
  down — a dropped SSE client must never take down the detector, and that intent lives
  in a comment at the point of the `except`.

## Definition of done

- The numbers are right, and there is a test that would notice if they stopped being.
- New constants live in `detect/config.py` with a comment saying why that value.
- Nothing new was added to `indicators/` or `detect/` that imports I/O.
- Anything deliberately left out is recorded in section 11 of
  [docs/DESIGN.md](docs/DESIGN.md), with the reason and the trigger for revisiting.

## Guardrails (do not cross)

- **Never read `.env`.** Nothing here needs secrets today — Hyperliquid's info API is
  unauthenticated — so if a `.env` ever appears, it holds something someone
  deliberately kept out of the code.
- **The web app is private and read-only over HTTP.** It binds to `127.0.0.1`, is
  reached only over Tailscale, and is never exposed publicly. No endpoint writes; the
  detector is the only writer to the database.
- **Leave `macd_searcher` alone.** It shares the droplet and its IP. Every Hyperliquid
  REST call goes through the pacer, so this app can never eat its rate limit. Never
  touch its files, database, cron, systemd unit, or `tailscale serve` handler.
- **Persistence is not best-effort.** In `macd_searcher`, DB logging is best-effort so a
  glitch never breaks a scan. Here the stored windows *are* the outcome record
  (invariant 5), so a failed write is a real failure: surface it, never swallow it.
  What *is* best-effort is everything downstream of the detector: SSE clients.

## Workflow notes

- Run the suite with `uv run pytest -q` and lint with `uv run ruff check`. Once `ui/`
  exists, type-check it with `npm --prefix ui run build` before shipping UI changes.
- Work through [docs/PLAN.md](docs/PLAN.md) in order. A milestone's **Done when** line
  is the gate, not the number of boxes ticked.
- Keep changes small and reversible. Tick boxes in the plan as they land, and move its
  **Now:** line when a milestone closes.
