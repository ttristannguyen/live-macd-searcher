"""The window state machine for one symbol (DESIGN §4). Pure: closed bars in, events out.

Two slots, never a list. A symbol has at most one run that is *contracting* (forming or
`active`) and at most one window being *followed* after its cross (`crossed`). A
followed window always resolves before the contracting slot can cross into its place
(DESIGN §4, "At most two windows per symbol"), and `_cross` checks that rather than
trusting it.
"""

from dataclasses import dataclass, replace
from typing import NamedTuple

from ..indicators.bollinger import Bands
from .classify import classify_asset, classify_band, classify_regime, side_of
from .config import BACKFILL_BARS, MIN_PEAK_PCT, MIN_RUN_BARS, POST_CROSS_BARS
from .normalise import band_offset, pct_of_close
from .strength import score
from .vocabulary import Side, State
from .window import Window, WindowEvent


class BarReading(NamedTuple):
    """One closed bar with its indicator values: what the detector steps on."""

    open_time: int
    high: float
    low: float
    close: float
    macd: float
    signal: float
    hist: float
    bands: Bands | None  # None only before the bands have a full period of closes


@dataclass
class _Run:
    """The contracting slot. Becomes a published `Window` once it passes the gates."""

    side: Side
    started_at: int  # the peak bar
    peak_pct: float
    bars: int = 0
    window: Window | None = None  # None while still forming: never emitted


class WindowDetector:
    def __init__(self, symbol: str, warmup_bars: int = BACKFILL_BARS) -> None:
        self.symbol = symbol
        self.asset_class = classify_asset(symbol)
        self.warmup_bars = warmup_bars
        self.bars_seen = 0
        self.previous: BarReading | None = None
        self.contracting: _Run | None = None
        self.following: Window | None = None

    @property
    def warming(self) -> bool:
        return self.bars_seen < self.warmup_bars

    def step(self, bar: BarReading) -> list[WindowEvent]:
        """Apply one closed bar. Returns what happened to this symbol's windows."""
        previous = self.previous
        if previous is not None and bar.open_time <= previous.open_time:
            # A duplicate or out-of-order bar. Ignoring it makes replay a no-op (DESIGN §6).
            return []
        self.previous = bar
        self.bars_seen += 1

        # Nothing is emitted while warming (invariant 4). A bar is trustworthy once it has
        # `warmup_bars` of history including itself, and every step compares a bar with
        # the one before, so the first step runs on the bar after that.
        if previous is None or self.bars_seen <= self.warmup_bars:
            return []
        if bar.bands is None:
            raise ValueError(f"{self.symbol}: warm bar at {bar.open_time} has no bands")

        # Followed window first: when this bar's sign change both crosses the contracting
        # window and reverses the followed one, the followed slot must be free first.
        return self._follow(bar) + self._contract(previous, bar)

    # --- contracting slot --------------------------------------------------------------

    def _contract(self, previous: BarReading, bar: BarReading) -> list[WindowEvent]:
        run = self.contracting
        if run is None:
            if not _shrank_with_sign_held(previous.hist, bar.hist):
                return []
            # The bar before the first shrink step is the peak: the window's origin.
            run = _Run(
                side=side_of(bar.hist),
                started_at=previous.open_time,
                peak_pct=pct_of_close(previous.hist, previous.close),
            )
            self.contracting = run
            return self._shrink(run, previous, bar)

        if _has_crossed(run.side, bar.hist):
            return self._cross(run, bar)
        if abs(bar.hist) < abs(previous.hist):
            return self._shrink(run, previous, bar)
        # Re-expanded, or held flat: "shrinking" means strictly shrinking (DESIGN §11).
        return self._fail(run, bar)

    def _shrink(self, run: _Run, previous: BarReading, bar: BarReading) -> list[WindowEvent]:
        run.bars += 1
        latest = _latest(run.side, bar)
        regime = classify_regime(run.side, bar.macd, bar.signal)
        # The MACD line itself moving toward the signal line, not just the signal drifting.
        line_turn = bar.macd > previous.macd if run.side == "bullish" else bar.macd < previous.macd
        measured = {
            "bars": run.bars,
            "regime": regime,
            "line_turn": line_turn,
            "strength": score(
                hist_pct=latest["hist_pct"],
                peak_pct=run.peak_pct,
                bars=run.bars,
                regime=regime,
                band=latest["band"],
                line_turn=line_turn,
            ),
            **latest,
        }

        if run.window is None:
            if run.bars < MIN_RUN_BARS or abs(run.peak_pct) < MIN_PEAK_PCT:
                return []
            window = Window(
                symbol=self.symbol,
                asset_class=self.asset_class,
                side=run.side,
                state="active",
                started_at=run.started_at,
                peak_pct=run.peak_pct,
                price_at_open=bar.close,
                **measured,
            )
            kind = "opened"
        else:
            window = replace(run.window, **measured)
            kind = "updated"

        run.window = _note_through(window, bar)
        return [WindowEvent(kind, run.window)]

    def _cross(self, run: _Run, bar: BarReading) -> list[WindowEvent]:
        self.contracting = None
        if run.window is None:
            return []  # never published, so there is nothing to follow
        if self.following is not None:
            raise RuntimeError(
                f"{self.symbol}: a window crossed at {bar.open_time} while another was "
                "still being followed — DESIGN §4 says this cannot happen"
            )
        latest = _latest(run.side, bar)
        crossed = replace(
            run.window,
            state="crossed",
            crossed_at=bar.open_time,
            price_at_cross=bar.close,
            band_at_cross=latest["band"],
            **latest,
        )
        self.following = _note_through(crossed, bar)
        return [WindowEvent("crossed", self.following)]

    def _fail(self, run: _Run, bar: BarReading) -> list[WindowEvent]:
        self.contracting = None
        if run.window is None:
            return []
        latest = _note_through(replace(run.window, **_latest(run.side, bar)), bar)
        return [WindowEvent("resolved", _resolve(latest, "failed", bar))]

    # --- followed slot -----------------------------------------------------------------

    def _follow(self, bar: BarReading) -> list[WindowEvent]:
        """One bar after the cross. The cross bar itself is the reference, never checked."""
        window = self.following
        if window is None:
            return []
        assert bar.bands is not None and window.price_at_cross is not None

        favourable, adverse = _excursions(window.side, window.price_at_cross, bar)
        window = _note_through(
            replace(
                window,
                bars_since_cross=window.bars_since_cross + 1,
                max_favourable_pct=_max(window.max_favourable_pct, favourable),
                max_adverse_pct=_max(window.max_adverse_pct, adverse),
                **_latest(window.side, bar),
            ),
            bar,
        )

        # Checked in this order (DESIGN §4). Target first is a choice made under
        # ignorance: a closed bar cannot say whether its high came before its close.
        outcome: State | None = None
        if _touched_target(window.side, bar, bar.bands):
            outcome = "hit"
        elif _crossed_back(window.side, bar.hist):
            outcome = "reversed"
        elif window.bars_since_cross >= POST_CROSS_BARS:
            outcome = "expired"

        if outcome is None:
            self.following = window
            return [WindowEvent("updated", window)]
        self.following = None
        return [WindowEvent("resolved", _resolve(window, outcome, bar))]


# --- pure helpers ----------------------------------------------------------------------


def _shrank_with_sign_held(previous_hist: float, hist: float) -> bool:
    return previous_hist * hist > 0 and abs(hist) < abs(previous_hist)


def _has_crossed(side: Side, hist: float) -> bool:
    """Whether the histogram has left the side the window started on.

    Zero counts as a sign change in both directions (DESIGN §4): it is a cross here, and
    a cross back in `_crossed_back`. That is deliberate, and it is what guarantees a
    followed window resolves on any bar where a contracting one crosses.
    """
    return hist >= 0 if side == "bullish" else hist <= 0


def _crossed_back(side: Side, hist: float) -> bool:
    """Whether the histogram is zero or back on the side the window started on."""
    return hist <= 0 if side == "bullish" else hist >= 0


def _touched_target(side: Side, bar: BarReading, bands: Bands) -> bool:
    """The outer band on the far side, as of this bar."""
    return bar.high >= bands.upper if side == "bullish" else bar.low <= bands.lower


def _latest(side: Side, bar: BarReading) -> dict:
    """The fields that track the latest closed bar, before and after the cross."""
    assert bar.bands is not None
    offset = band_offset(bar.close, bar.bands)
    return {
        "updated_at": bar.open_time,
        "hist_pct": pct_of_close(bar.hist, bar.close),
        "macd_pct": pct_of_close(bar.macd, bar.close),
        "signal_pct": pct_of_close(bar.signal, bar.close),
        "band": classify_band(side, offset),
        "band_offset": offset,
    }


def _note_through(window: Window, bar: BarReading) -> Window:
    """Record the first bar, once the window is open, whose close went through the middle."""
    if window.band_through_at is None and window.band == "through":
        return replace(window, band_through_at=bar.open_time)
    return window


def _excursions(side: Side, price_at_cross: float, bar: BarReading) -> tuple[float, float]:
    """This bar's best and worst price relative to the cross, as % of the cross price."""
    up = pct_of_close(bar.high - price_at_cross, price_at_cross)
    down = pct_of_close(price_at_cross - bar.low, price_at_cross)
    return (up, down) if side == "bullish" else (down, up)


def _max(current: float | None, candidate: float) -> float:
    return candidate if current is None else max(current, candidate)


def _resolve(window: Window, state: State, bar: BarReading) -> Window:
    return replace(window, state=state, resolved_at=bar.open_time, price_at_resolve=bar.close)
