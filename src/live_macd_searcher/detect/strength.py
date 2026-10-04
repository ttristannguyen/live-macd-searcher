"""Strength: one 0–100 score for a contracting window (DESIGN §3).

Pure: numbers in, a number out. The weights and multipliers it uses are opinions,
defined once in `config.py`, and the outcome record exists to replace them with
evidence.
"""

from .config import (
    BAND_MULTIPLIER,
    LINE_TURN_BONUS,
    PERSISTENCE_SATURATES_AT,
    REGIME_MULTIPLIER,
    WEIGHTS,
)
from .vocabulary import Band, Regime


def score(
    *,
    hist_pct: float,
    peak_pct: float,
    bars: int,
    regime: Regime,
    band: Band,
    line_turn: bool,
) -> float:
    """Score a window after `bars` shrink steps from `peak_pct` down to `hist_pct`.

    `hist_pct` and `peak_pct` share a sign and `abs(hist_pct) < abs(peak_pct)`: the
    window's definition guarantees both, so the ratios below are well defined.
    """
    # How much of the momentum has unwound, in [0, 1).
    decay = 1 - hist_pct / peak_pct
    # A run that has held for several bars is a trend, not a wobble.
    persistence = min(bars, PERSISTENCE_SATURATES_AT) / PERSISTENCE_SATURATES_AT
    # Bars until the cross if the average shrink rate so far continues.
    bars_to_cross = hist_pct / ((peak_pct - hist_pct) / bars)
    proximity = 1 / (1 + bars_to_cross)

    weighted = (
        WEIGHTS["decay"] * decay
        + WEIGHTS["persistence"] * persistence
        + WEIGHTS["proximity"] * proximity
    )
    multiplied = (
        weighted
        * REGIME_MULTIPLIER[regime]
        * BAND_MULTIPLIER[band]
        * (LINE_TURN_BONUS if line_turn else 1.0)
    )
    return 100 * min(max(multiplied, 0.0), 1.0)
