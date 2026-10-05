"""Closed candles → indicators → detector, for tests that need the whole pure path.

M5's `SymbolState` is the real version of this; until then, tests share this one.
"""

from live_macd_searcher.detect.config import BOLLINGER_PERIOD, BOLLINGER_WIDTH
from live_macd_searcher.detect.detector import BarReading, WindowDetector
from live_macd_searcher.detect.window import WindowEvent
from live_macd_searcher.indicators.bollinger import BollingerState
from live_macd_searcher.indicators.macd import MacdState
from live_macd_searcher.market import Candle


class Pipeline:
    def __init__(self, symbol: str) -> None:
        self.macd = MacdState()
        self.bands = BollingerState(BOLLINGER_PERIOD, BOLLINGER_WIDTH)
        self.detector = WindowDetector(symbol)

    def step(self, candle: Candle) -> list[WindowEvent]:
        point = self.macd.update(candle.close)
        reading = BarReading(
            candle.open_time, candle.high, candle.low, candle.close,
            point.macd, point.signal, point.hist, self.bands.update(candle.close),
        )  # fmt: skip
        return self.detector.step(reading)
