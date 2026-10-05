"""One symbol's indicators and detector, fed one closed bar at a time. Pure.

The runtime holds one per symbol in a plain dict (DESIGN §5): closed bars go in through
`on_bar()`, and the forming bar is read through `peek()`, which commits nothing.
"""

from dataclasses import dataclass

from ..indicators.bollinger import BollingerState
from ..indicators.macd import MacdState
from ..market import Candle
from .config import BOLLINGER_PERIOD, BOLLINGER_WIDTH
from .detector import BarReading, WindowDetector
from .normalise import band_offset, pct_of_close
from .window import WindowEvent


@dataclass(frozen=True)
class Provisional:
    """A reading from the still-forming bar: displayed, never persisted, never acted on."""

    symbol: str
    open_time: int
    close: float
    hist_pct: float
    macd_pct: float
    signal_pct: float
    band_offset: float


class SymbolState:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.macd = MacdState()
        self.bands = BollingerState(BOLLINGER_PERIOD, BOLLINGER_WIDTH)
        self.detector = WindowDetector(symbol)
        self.last_bar: Candle | None = None

    @property
    def warming(self) -> bool:
        return self.detector.warming

    def on_bar(self, candle: Candle) -> list[WindowEvent]:
        """Commit one closed bar. A duplicate or out-of-order bar changes nothing — the
        indicators are guarded here, not just the detector, since an EMA can't un-see one."""
        if self.last_bar is not None and candle.open_time <= self.last_bar.open_time:
            return []
        self.last_bar = candle
        point = self.macd.update(candle.close)
        reading = BarReading(
            candle.open_time, candle.high, candle.low, candle.close,
            point.macd, point.signal, point.hist, self.bands.update(candle.close),
        )  # fmt: skip
        return self.detector.step(reading)

    def peek(self, forming: Candle) -> Provisional | None:
        """The forming bar's reading, without committing it (invariant 1). None while
        warming: a half-converged reading is not worth showing (invariant 4)."""
        if self.warming:
            return None
        point = self.macd.peek(forming.close)
        bands = self.bands.peek(forming.close)
        assert bands is not None  # warm means far more than a full Bollinger period
        return Provisional(
            symbol=self.symbol,
            open_time=forming.open_time,
            close=forming.close,
            hist_pct=pct_of_close(point.hist, forming.close),
            macd_pct=pct_of_close(point.macd, forming.close),
            signal_pct=pct_of_close(point.signal, forming.close),
            band_offset=band_offset(forming.close, bands),
        )
