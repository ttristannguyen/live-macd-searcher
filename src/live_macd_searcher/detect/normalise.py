"""Normalise before comparing (invariant 3).

Raw MACD values are in price units and mean nothing across symbols. Everything that is
ranked, thresholded, or shown beside another symbol goes through here first.
"""

from ..indicators.bollinger import Bands


def pct_of_close(value: float, close: float) -> float:
    """A price-unit value as a percentage of close: `hist_pct`, `macd_pct`, `signal_pct`."""
    return value / close * 100


def band_offset(close: float, bands: Bands) -> float:
    """Where close sits, in band widths from the middle: 0 at the middle, ±0.5 at the bands.

    Scale-free and volatility-adjusted already. If the bands have zero width (every close
    in the period identical), close is on the middle band, so the offset is defined as 0
    rather than divided by zero (DESIGN §2).
    """
    width = bands.upper - bands.lower
    if width == 0:
        return 0.0
    return (close - bands.middle) / width
