"""The strength score, against values worked out by hand from DESIGN §3."""

import pytest

from live_macd_searcher.detect.strength import score

# A bullish window three shrink steps from a -1.0% peak down to -0.4%:
#   decay       = 1 - (-0.4 / -1.0)                  = 0.6
#   persistence = min(3, 6) / 6                       = 0.5
#   bars_to_cross = -0.4 / ((-1.0 - -0.4) / 3)        = 2   ->  proximity = 1/3
#   weighted    = 0.40*0.6 + 0.25*0.5 + 0.35*(1/3)    = 0.481666...
BASE = {"hist_pct": -0.4, "peak_pct": -1.0, "bars": 3, "regime": "reversal", "band": "through"}
WEIGHTED = 0.40 * 0.6 + 0.25 * 0.5 + 0.35 / 3


def test_score_matches_hand_calculation():
    assert score(**BASE, line_turn=False) == pytest.approx(100 * WEIGHTED)


def test_line_turn_is_a_ten_percent_bonus():
    assert score(**BASE, line_turn=True) == pytest.approx(100 * WEIGHTED * 1.10)


def test_bearish_mirror_scores_the_same():
    mirrored = {**BASE, "hist_pct": 0.4, "peak_pct": 1.0}
    assert score(**mirrored, line_turn=False) == pytest.approx(score(**BASE, line_turn=False))


@pytest.mark.parametrize(
    ("regime", "band", "multiplier"),
    [
        ("continuation", "through", 0.85),
        ("transition", "through", 0.70),
        ("reversal", "near", 0.95),
        ("reversal", "far", 0.80),
        ("transition", "far", 0.70 * 0.80),
    ],
)
def test_regime_and_band_multiply(regime, band, multiplier):
    result = score(**{**BASE, "regime": regime, "band": band}, line_turn=False)
    assert result == pytest.approx(100 * WEIGHTED * multiplier)


def test_persistence_saturates_at_six_bars():
    # Persistence is 1.0 at both 6 and 12 bars, so the two scores differ only by
    # proximity: the same unwind spread over twice the bars is a slower pace, so the
    # extrapolated cross is further away.
    def at(bars: int) -> float:
        return score(**{**BASE, "bars": bars}, line_turn=False)

    def proximity(bars: int) -> float:
        return 1 / (1 + (-0.4 / ((-1.0 + 0.4) / bars)))

    assert at(12) - at(6) == pytest.approx(100 * 0.35 * (proximity(12) - proximity(6)))


def test_score_is_clamped_to_100():
    # Nearly fully unwound, long, imminent, with every bonus: the raw product exceeds 1.
    result = score(hist_pct=-0.001, peak_pct=-1.0, bars=6, regime="reversal", band="through",
                   line_turn=True)  # fmt: skip
    assert result == 100.0
