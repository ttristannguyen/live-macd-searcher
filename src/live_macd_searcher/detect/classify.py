"""The categorical fields: side, regime, band, and asset class."""

from .config import NEAR_MIDDLE_BAND
from .vocabulary import AssetClass, Band, Regime, Side


def side_of(hist: float) -> Side:
    """The side a shrinking histogram of this sign points to (DESIGN §2).

    Negative shrinking toward zero → a bullish cross approaches; positive → bearish.
    Only meaningful for non-zero `hist`.
    """
    return "bullish" if hist < 0 else "bearish"


def classify_regime(side: Side, macd: float, signal: float) -> Regime:
    """Where the MACD and signal lines sit relative to zero, read for the window's side.

    Both lines on the losing side of zero is a `reversal` (a trend running out of steam);
    both on the winning side is a `continuation` (a pullback in a trend); straddling, or
    exactly on, zero is a `transition`.
    """
    if macd < 0 and signal < 0:
        return "reversal" if side == "bullish" else "continuation"
    if macd > 0 and signal > 0:
        return "continuation" if side == "bullish" else "reversal"
    return "transition"


def classify_band(side: Side, offset: float) -> Band:
    """Where close sits relative to the middle band, read in the window's direction.

    `toward` is positive once price is past the middle band in the direction the window
    expects. A close exactly on the middle band is `near`: `through` means strictly past.
    """
    toward = offset if side == "bullish" else -offset
    if toward > 0:
        return "through"
    if toward >= -NEAR_MIDDLE_BAND:
        return "near"
    return "far"


# HIP-3 `xyz` markets by base symbol, as in macd_searcher's classify.py. Heuristic and
# extended by hand as the DEX lists new markets.
_COMMODITIES = {
    "GOLD", "SILVER", "COPPER", "ALUMINIUM", "ALUMINUM", "PLATINUM", "PALLADIUM",
    "BRENTOIL", "CL", "WTI", "NATGAS", "CORN", "WHEAT", "SOYBEAN", "SUGAR",
    "COFFEE", "COCOA", "COTTON",
}  # fmt: skip
_FX = {"EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "CNH", "DXY", "MXN", "SEK", "NOK"}
_INDICES = {"XYZ100", "SP500", "NAS100", "US30", "DJI", "NDX"}


def classify_asset(symbol: str) -> AssetClass:
    """Asset class from a Hyperliquid symbol: `BTC` is crypto, `xyz:TSLA` an equity.

    Core perps carry no `dex:` prefix and are all crypto. An unrecognised prefixed symbol
    is an equity, the most common case on `xyz`.
    """
    if ":" not in symbol:
        return "crypto"
    base = symbol.split(":", 1)[1].upper()
    if base in _COMMODITIES:
        return "commodity"
    if base in _FX:
        return "fx"
    if base in _INDICES:
        return "index"
    return "equity"
