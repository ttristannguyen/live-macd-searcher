"""Indicator maths: golden vectors, live-vs-batch agreement, and side-effect-free peeks.

Everything above this layer is only as correct as these numbers, so they are pinned
against an independent implementation rather than against themselves.
"""

import pytest

from live_macd_searcher.indicators.bollinger import BollingerState, bollinger
from live_macd_searcher.indicators.ema import EmaState, ema
from live_macd_searcher.indicators.macd import MacdState, macd

TOLERANCE = 1e-9

# A synthetic 60-bar close series: a slow sine for trend, a faster cosine for wiggle,
# and drift, rounded to cents —
#   round(100 + 8 * sin(i / 4) + 0.15 * i + 3 * cos(i / 1.7), 2)
# Written out as literals so the input can never depend on the platform's libm. Its
# histogram changes sign inside the series, so both sides are covered.
CLOSES = [
    103.00, 104.62, 105.29, 105.33, 105.22, 105.40, 106.10, 107.24, 108.45, 109.22,
    109.05, 107.65, 105.07, 101.70, 98.18, 95.20, 93.35, 92.87, 93.69, 95.40,
    97.42, 99.21, 100.45, 101.10, 101.42, 101.87, 102.87, 104.70, 107.29, 110.28,
    113.08, 115.06, 115.71, 114.87, 112.71, 109.75, 106.64, 104.02, 102.29, 101.55,
    101.55, 101.86, 101.99, 101.65, 100.80, 99.70, 98.85, 98.79, 99.91, 102.30,
    105.71, 109.57, 113.19, 115.92, 117.35, 117.43, 116.46, 114.95, 113.46, 112.43,
]  # fmt: skip

# Expected values below were generated with pandas 3.0.5, an implementation independent
# of ours:
#   ema    = s.ewm(span=N, adjust=False).mean()
#   macd   = ema(12) - ema(26);  signal = macd.ewm(span=9, adjust=False).mean()
#   middle = s.rolling(20).mean();  sd = s.rolling(20).std(ddof=0)
EMA_12 = {
    0: 103.0,
    1: 103.24923076923076,
    11: 106.69832155736302,
    30: 104.77927292043609,
    59: 111.80268903935415,
}

MACD = {  # index: (macd, signal, hist)
    0: (0.0, 0.0, 0.0),
    1: (0.12923076923077303, 0.02584615384615461, 0.10338461538461843),
    2: (0.2824545255314632, 0.07716782818321634, 0.2052866973482469),
    25: (-1.0003414297481896, -1.4295089921599826, 0.429167562411793),
    26: (-0.6901870097085663, -1.2816445956696993, 0.5914575859611331),
    40: (0.3628739448555649, 1.458459863463812, -1.095585918608247),
    59: (2.938621822719682, 2.155984763135741, 0.7826370595839411),
}

BOLLINGER_20_2 = {  # index: (middle, upper, lower)
    19: (102.60150000000002, 113.60364574526263, 91.5993542547374),
    20: (102.3225, 113.55075342606766, 91.09424657393235),
    40: (106.321, 117.1866815708909, 95.4553184291091),
    59: (107.1935, 121.14326741741588, 93.24373258258412),
}


# --- golden vectors --------------------------------------------------------------


def test_ema_matches_golden_vectors():
    result = ema(CLOSES, 12)
    for index, expected in EMA_12.items():
        assert result[index] == pytest.approx(expected, abs=TOLERANCE), f"bar {index}"


def test_macd_matches_golden_vectors():
    result = macd(CLOSES)
    for index, expected in MACD.items():
        assert tuple(result[index]) == pytest.approx(expected, abs=TOLERANCE), f"bar {index}"


def test_bollinger_matches_golden_vectors():
    result = bollinger(CLOSES, period=20, width=2.0)
    for index, expected in BOLLINGER_20_2.items():
        assert tuple(result[index]) == pytest.approx(expected, abs=TOLERANCE), f"bar {index}"


def test_bollinger_is_none_until_the_period_is_full():
    result = bollinger(CLOSES, period=20, width=2.0)
    assert result[:19] == [None] * 19
    assert result[19] is not None


def test_bollinger_on_flat_closes_has_zero_width_exactly():
    # The band classifier defines zero-width bands specially (DESIGN §2), so a flat run
    # must produce exactly equal bands, not ones that differ by float dust.
    bands = bollinger([101.37] * 20, period=20, width=2.0)[-1]
    assert bands.upper == bands.middle == bands.lower == 101.37


# --- live path agrees with batch -------------------------------------------------


def test_incremental_ema_equals_batch():
    state = EmaState(12)
    assert [state.update(close) for close in CLOSES] == ema(CLOSES, 12)


def test_incremental_macd_equals_batch():
    state = MacdState()
    assert [state.update(close) for close in CLOSES] == macd(CLOSES)


def test_incremental_bollinger_equals_batch():
    state = BollingerState(period=20, width=2.0)
    assert [state.update(close) for close in CLOSES] == bollinger(CLOSES, 20, 2.0)


# --- peek: the provisional reading, with no side effects -------------------------

# Each case: a fresh state, and a snapshot of everything that update() can change.
PEEKABLE = {
    "ema": (lambda: EmaState(12), lambda s: s.value),
    "macd": (MacdState, lambda s: (s.fast.value, s.slow.value, s.signal.value)),
    "bollinger": (lambda: BollingerState(20, 2.0), lambda s: list(s.closes)),
}


@pytest.mark.parametrize("name", PEEKABLE)
def test_peek_leaves_state_unchanged(name):
    make, snapshot = PEEKABLE[name]
    state = make()
    for close in CLOSES[:30]:
        state.update(close)

    before = snapshot(state)
    state.peek(999.0)
    assert snapshot(state) == before


@pytest.mark.parametrize("name", PEEKABLE)
def test_peek_predicts_the_next_update(name):
    # The provisional reading of a forming bar is exactly what that bar will read once
    # it closes at the same price.
    make, _ = PEEKABLE[name]
    state = make()
    for close in CLOSES[:30]:
        state.update(close)

    assert state.peek(CLOSES[30]) == state.update(CLOSES[30])


def test_bollinger_peek_completes_the_window():
    # With period - 1 closes committed, the peeked close fills the window.
    state = BollingerState(period=20, width=2.0)
    for close in CLOSES[:19]:
        state.update(close)

    assert state.peek(CLOSES[19]) == pytest.approx(BOLLINGER_20_2[19], abs=TOLERANCE)


def test_peek_on_an_empty_ema_is_the_value_itself():
    # The seed: the first value is the EMA (see ema.py).
    assert EmaState(12).peek(42.0) == 42.0
