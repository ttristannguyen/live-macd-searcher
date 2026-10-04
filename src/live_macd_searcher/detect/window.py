"""A published window, and the events the detector emits about it.

`Window` is frozen: every transition makes a new one, so an event handed to the store
or the stream can never change underneath it. Fields mirror the `windows` table
(DESIGN §7). All `*_at` fields are exchange `open_time`s in milliseconds.
"""

from dataclasses import dataclass

from .vocabulary import AssetClass, Band, EventKind, Regime, Side, State


@dataclass(frozen=True)
class Window:
    symbol: str
    asset_class: AssetClass
    side: Side
    state: State
    started_at: int  # the peak bar
    peak_pct: float  # signed, like hist_pct
    price_at_open: float  # close of the bar that published the window

    # Measured on every shrink step, then frozen at the cross: together they are the
    # prediction the outcome is checked against.
    bars: int
    regime: Regime
    line_turn: bool
    strength: float

    # The latest closed bar applied. Kept current after the cross too.
    updated_at: int
    hist_pct: float
    macd_pct: float
    signal_pct: float
    band: Band
    band_offset: float
    band_through_at: int | None = None  # first close through the middle band, once open

    # Set at the cross, and while following the move.
    crossed_at: int | None = None
    price_at_cross: float | None = None
    band_at_cross: Band | None = None
    bars_since_cross: int = 0
    max_favourable_pct: float | None = None  # after the cross, % of price_at_cross
    max_adverse_pct: float | None = None

    # Set at resolution.
    resolved_at: int | None = None
    price_at_resolve: float | None = None  # close of the resolving bar


@dataclass(frozen=True)
class WindowEvent:
    kind: EventKind
    window: Window
