"""Indicator values for charts, recomputed from stored bars.

They aren't stored: they are a pure function of the bars. Computed over *all* of a
symbol's stored bars — from the same earliest bar the runtime seeds from — they are
exactly the values the detector saw, so a chart can never disagree with the board.
"""

from ..detect.config import BOLLINGER_PERIOD, BOLLINGER_WIDTH
from ..indicators.bollinger import bollinger
from ..indicators.macd import macd
from ..market import Candle
from .models import SeriesBar


def indicator_series(candles: list[Candle]) -> list[SeriesBar]:
    closes = [candle.close for candle in candles]
    points = macd(closes)
    bands = bollinger(closes, BOLLINGER_PERIOD, BOLLINGER_WIDTH)
    return [
        SeriesBar(
            open_time=candle.open_time,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            macd=point.macd,
            signal=point.signal,
            hist=point.hist,
            middle=band.middle if band else None,
            upper=band.upper if band else None,
            lower=band.lower if band else None,
        )
        for candle, point, band in zip(candles, points, bands, strict=True)
    ]
