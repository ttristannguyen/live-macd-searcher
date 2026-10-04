"""Normalisation and the categorical fields: side, regime, band, asset class."""

import pytest

from live_macd_searcher.detect.classify import (
    classify_asset,
    classify_band,
    classify_regime,
    side_of,
)
from live_macd_searcher.detect.config import NEAR_MIDDLE_BAND
from live_macd_searcher.detect.normalise import band_offset, pct_of_close
from live_macd_searcher.indicators.bollinger import Bands


def test_pct_of_close_makes_symbols_comparable():
    # The same 1% histogram on BTC and on a 42-cent coin reads the same.
    assert pct_of_close(600.0, 60_000.0) == pytest.approx(1.0)
    assert pct_of_close(0.0042, 0.42) == pytest.approx(1.0)


def test_side_of_a_shrinking_histogram():
    assert side_of(-0.3) == "bullish"
    assert side_of(0.3) == "bearish"


@pytest.mark.parametrize(
    ("side", "macd", "signal", "expected"),
    [
        ("bullish", -2.0, -1.0, "reversal"),
        ("bullish", 2.0, 1.0, "continuation"),
        ("bullish", 1.0, -1.0, "transition"),
        ("bearish", 2.0, 1.0, "reversal"),
        ("bearish", -2.0, -1.0, "continuation"),
        ("bearish", -1.0, 1.0, "transition"),
        # Exactly on zero is neither side of it.
        ("bullish", 0.0, -1.0, "transition"),
        ("bearish", 1.0, 0.0, "transition"),
    ],
)
def test_classify_regime(side, macd, signal, expected):
    assert classify_regime(side, macd, signal) == expected


# --- band --------------------------------------------------------------------------

BANDS = Bands(middle=100.0, upper=110.0, lower=90.0)  # 20 wide


@pytest.mark.parametrize(
    ("close", "expected_offset"),
    [(100.0, 0.0), (110.0, 0.5), (90.0, -0.5), (105.0, 0.25)],
)
def test_band_offset_is_in_band_widths_from_the_middle(close, expected_offset):
    assert band_offset(close, BANDS) == pytest.approx(expected_offset)


def test_band_offset_of_zero_width_bands_is_zero_not_a_division_error():
    flat = Bands(middle=50.0, upper=50.0, lower=50.0)
    assert band_offset(50.0, flat) == 0.0
    assert classify_band("bullish", band_offset(50.0, flat)) == "near"


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (0.01, "through"),
        (0.0, "near"),  # exactly on the middle band is not through it
        (-NEAR_MIDDLE_BAND, "near"),
        (-NEAR_MIDDLE_BAND - 1e-9, "far"),
        (-0.5, "far"),
    ],
)
def test_classify_band_bullish(offset, expected):
    assert classify_band("bullish", offset) == expected


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (-0.01, "through"),
        (0.0, "near"),
        (NEAR_MIDDLE_BAND, "near"),
        (NEAR_MIDDLE_BAND + 1e-9, "far"),
        (0.5, "far"),
    ],
)
def test_classify_band_bearish_is_the_mirror(offset, expected):
    assert classify_band("bearish", offset) == expected


# --- asset class -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("BTC", "crypto"),
        ("kPEPE", "crypto"),
        ("xyz:GOLD", "commodity"),
        ("xyz:EUR", "fx"),
        ("xyz:XYZ100", "index"),
        ("xyz:TSLA", "equity"),
        ("xyz:SOMETHINGNEW", "equity"),  # unknown xyz markets default to equity
    ],
)
def test_classify_asset(symbol, expected):
    assert classify_asset(symbol) == expected
