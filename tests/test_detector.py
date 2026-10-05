"""The window state machine, driven by hand-written histogram sequences (DESIGN §4).

No fixtures, no DB, no clock. Every bar closes at 100, so `hist_pct` equals `hist` and
each sequence reads directly in percent. Bands default to 90/100/110: close sits on the
middle band (`near`) and the targets are far enough away to touch only on purpose.
"""

import pytest

from live_macd_searcher.detect.config import BACKFILL_BARS, MIN_PEAK_PCT, POST_CROSS_BARS
from live_macd_searcher.detect.detector import BarReading, WindowDetector
from live_macd_searcher.indicators.bollinger import Bands

HOUR = 3_600_000
BANDS = Bands(middle=100.0, upper=110.0, lower=90.0)


def bar(t: int, hist: float, *, close=100.0, high=None, low=None, macd=0.0, bands=BANDS):
    return BarReading(
        open_time=t * HOUR,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        macd=macd,
        signal=macd - hist,
        hist=hist,
        bands=bands,
    )


def feed(bars, symbol="BTC"):
    """Step a fresh, already-warm detector through `bars`; return each bar's events."""
    detector = WindowDetector(symbol, warmup_bars=0)
    return detector, [detector.step(b) for b in bars]


def kinds(events_per_bar):
    return [[(e.kind, e.window.state) for e in events] for events in events_per_bar]


def hists(*values):
    return [bar(t, h) for t, h in enumerate(values)]


# --- contracting -------------------------------------------------------------------


def test_window_opens_after_min_run_bars_shrink_steps():
    _, events = feed(hists(-1.0, -0.8, -0.6))
    assert kinds(events) == [[], [], [("opened", "active")]]
    window = events[2][0].window
    assert window.side == "bullish"
    assert window.bars == 2
    assert window.started_at == 0  # the peak bar is the origin
    assert window.peak_pct == pytest.approx(-1.0)
    assert window.price_at_open == 100.0


def test_window_extends_on_each_further_shrink_step():
    _, events = feed(hists(-1.0, -0.8, -0.6, -0.4))
    assert kinds(events)[3] == [("updated", "active")]
    assert events[3][0].window.bars == 3
    assert events[3][0].window.hist_pct == pytest.approx(-0.4)


def test_bearish_window_is_the_mirror():
    _, events = feed(hists(1.0, 0.8, 0.6))
    assert kinds(events)[2] == [("opened", "active")]
    assert events[2][0].window.side == "bearish"


def test_re_expansion_fails_an_active_window():
    _, events = feed(hists(-1.0, -0.8, -0.6, -0.7))
    assert kinds(events)[3] == [("resolved", "failed")]
    failed = events[3][0].window
    assert failed.resolved_at == 3 * HOUR
    assert failed.price_at_resolve == 100.0


def test_a_flat_step_ends_the_run_because_shrinking_is_strict():
    _, events = feed(hists(-1.0, -0.8, -0.6, -0.6))
    assert kinds(events)[3] == [("resolved", "failed")]


def test_a_forming_run_that_re_expands_is_never_published():
    _, events = feed(hists(-1.0, -0.8, -0.9, -0.95))
    assert kinds(events) == [[], [], [], []]


def shrinking_from(peak: float) -> list[BarReading]:
    return hists(-peak, -peak * 0.8, -peak * 0.6, -peak * 0.4)


def test_noise_gate_never_publishes_a_shallow_peak():
    _, events = feed(shrinking_from(MIN_PEAK_PCT * 0.9))
    assert all(e == [] for e in events)


def test_noise_gate_publishes_a_peak_just_above_it():
    _, events = feed(shrinking_from(MIN_PEAK_PCT * 1.1))
    assert kinds(events)[2] == [("opened", "active")]


def test_a_new_run_starts_from_the_bar_where_the_last_one_ended():
    # -0.9 ends the first run by re-expanding, then becomes the next run's peak.
    _, events = feed(hists(-1.0, -0.8, -0.6, -0.9, -0.7, -0.5))
    assert kinds(events)[3] == [("resolved", "failed")]
    assert kinds(events)[5] == [("opened", "active")]
    assert events[5][0].window.started_at == 3 * HOUR
    assert events[5][0].window.peak_pct == pytest.approx(-0.9)


def test_regime_and_line_turn_are_measured_on_each_shrink_step():
    # Both lines below zero, MACD rising toward its signal: a bullish reversal turning.
    bars = [
        bar(0, -1.0, macd=-3.0),
        bar(1, -0.8, macd=-2.9),
        bar(2, -0.6, macd=-2.8),
    ]
    _, events = feed(bars)
    window = events[2][0].window
    assert window.regime == "reversal"
    assert window.line_turn is True


def test_line_turn_is_false_when_only_the_signal_line_moves():
    bars = [bar(0, -1.0, macd=-3.0), bar(1, -0.8, macd=-3.0), bar(2, -0.6, macd=-3.0)]
    _, events = feed(bars)
    assert events[2][0].window.line_turn is False


# --- the cross ---------------------------------------------------------------------


def test_sign_change_crosses_an_active_window():
    _, events = feed(hists(-1.0, -0.8, -0.6, 0.1))
    assert kinds(events)[3] == [("crossed", "crossed")]
    crossed = events[3][0].window
    assert crossed.crossed_at == 3 * HOUR
    assert crossed.price_at_cross == 100.0
    assert crossed.band_at_cross == "near"


def test_hist_exactly_zero_counts_as_a_cross():
    _, events = feed(hists(-1.0, -0.8, -0.6, 0.0))
    assert kinds(events)[3] == [("crossed", "crossed")]


def test_a_forming_run_that_crosses_is_never_published():
    _, events = feed(hists(-1.0, -0.8, 0.1))
    assert kinds(events) == [[], [], []]


def test_strength_regime_and_bars_freeze_at_the_cross():
    bars = [
        bar(0, -1.0, macd=-3.0),
        bar(1, -0.8, macd=-2.9),
        bar(2, -0.6, macd=-2.8),
        bar(3, 0.2, macd=1.0),  # cross: both lines now above zero
        bar(4, 0.3, macd=2.0),
    ]
    _, events = feed(bars)
    active = events[2][0].window
    for followed in (events[3][0].window, events[4][0].window):
        assert followed.strength == active.strength
        assert followed.regime == active.regime == "reversal"
        assert followed.bars == active.bars == 2
        assert followed.line_turn == active.line_turn


# --- following the move ------------------------------------------------------------

# A bullish window that crosses on bar 3, then whatever comes after.
CROSSED_ON_3 = [-1.0, -0.8, -0.6, 0.1]


def after_cross(*later_bars):
    return hists(*CROSSED_ON_3) + list(later_bars)


def test_followed_window_updates_while_nothing_resolves():
    _, events = feed(after_cross(bar(4, 0.2)))
    assert kinds(events)[4] == [("updated", "crossed")]
    assert events[4][0].window.bars_since_cross == 1


def test_touching_the_target_band_resolves_hit():
    _, events = feed(after_cross(bar(4, 0.2, high=110.0)))  # high reaches upper exactly
    assert kinds(events)[4] == [("resolved", "hit")]


def test_bearish_target_is_the_lower_band():
    bars = hists(1.0, 0.8, 0.6, -0.1) + [bar(4, -0.2, low=90.0)]
    _, events = feed(bars)
    assert kinds(events)[4] == [("resolved", "hit")]


def test_the_cross_bar_itself_is_never_checked_against_the_target():
    bars = hists(-1.0, -0.8, -0.6) + [bar(3, 0.1, high=115.0)]
    _, events = feed(bars)
    assert kinds(events)[3] == [("crossed", "crossed")]


def test_histogram_back_on_the_original_side_resolves_reversed():
    _, events = feed(after_cross(bar(4, -0.1)))
    assert kinds(events)[4] == [("resolved", "reversed")]


def test_histogram_back_to_zero_resolves_reversed():
    _, events = feed(after_cross(bar(4, 0.0)))
    assert kinds(events)[4] == [("resolved", "reversed")]


def test_target_wins_when_the_same_bar_also_reverses():
    # A closed bar can't say which came first; the rule is written down (DESIGN §4).
    _, events = feed(after_cross(bar(4, -0.1, high=111.0)))
    assert kinds(events)[4] == [("resolved", "hit")]


def test_expires_after_post_cross_bars_with_neither():
    # A flat positive histogram: no new run starts, nothing hits, nothing reverses.
    later = [bar(4 + i, 0.1) for i in range(POST_CROSS_BARS)]
    _, events = feed(after_cross(*later))
    assert kinds(events)[3 + POST_CROSS_BARS - 1] == [("updated", "crossed")]
    assert kinds(events)[3 + POST_CROSS_BARS] == [("resolved", "expired")]
    assert events[3 + POST_CROSS_BARS][0].window.bars_since_cross == POST_CROSS_BARS


def test_excursions_track_best_and_worst_price_after_the_cross():
    later = [bar(4, 0.2, high=104.0, low=97.0), bar(5, 0.3, high=102.0, low=99.0)]
    _, events = feed(after_cross(*later))
    window = events[5][0].window
    assert window.max_favourable_pct == pytest.approx(4.0)
    assert window.max_adverse_pct == pytest.approx(3.0)


def test_bearish_excursions_are_mirrored():
    bars = hists(1.0, 0.8, 0.6, -0.1) + [bar(4, -0.2, high=101.0, low=95.0)]
    _, events = feed(bars)
    window = events[4][0].window
    assert window.max_favourable_pct == pytest.approx(5.0)  # price fell: good for bearish
    assert window.max_adverse_pct == pytest.approx(1.0)


def test_band_through_at_records_the_first_close_through_the_middle():
    bars = [
        bar(0, -1.0, close=95.0),
        bar(1, -0.8, close=95.0),
        bar(2, -0.6, close=96.0),  # opens: below the middle band
        bar(3, -0.4, close=101.0),  # through the middle band
        bar(4, -0.2, close=102.0),
    ]
    _, events = feed(bars)
    assert events[2][0].window.band_through_at is None
    assert events[3][0].window.band_through_at == 3 * HOUR
    assert events[4][0].window.band_through_at == 3 * HOUR  # the first, not the latest


# --- two slots ---------------------------------------------------------------------


def test_opposite_window_crossing_reverses_the_followed_one_on_the_same_bar():
    # Bullish crosses on bar 3. The positive histogram then peaks and shrinks: a bearish
    # window opens while the bullish one is followed. When the bearish one crosses, the
    # histogram is back below zero, so the bullish one is reversed on that same bar.
    _, events = feed(hists(-1.0, -0.8, -0.6, 1.0, 0.8, 0.6, -0.1))
    assert kinds(events)[5] == [("updated", "crossed"), ("opened", "active")]
    assert [(e.kind, e.window.state, e.window.side) for e in events[6]] == [
        ("resolved", "reversed", "bullish"),
        ("crossed", "crossed", "bearish"),
    ]


# --- warm-up, replay, and bad input ------------------------------------------------


def test_nothing_is_emitted_while_warming():
    # A pattern that would open a window every four bars, fed through the full warm-up.
    pattern = [-1.0, -0.8, -0.6, -0.4]
    detector = WindowDetector("BTC")  # the real BACKFILL_BARS
    events = [detector.step(bar(t, pattern[t % 4])) for t in range(BACKFILL_BARS)]
    assert all(e == [] for e in events)
    assert not detector.warming

    later = [detector.step(bar(t, pattern[t % 4])) for t in range(BACKFILL_BARS, BACKFILL_BARS + 8)]
    assert any(e for e in later)


def test_warm_up_boundary_is_exact():
    # A histogram shrinking steadily from the very first bar. Nothing that began during
    # warm-up may carry over: the earliest possible peak is the last warm-up bar, so the
    # window opens MIN_RUN_BARS steps later — and not one bar earlier.
    detector = WindowDetector("BTC")
    events = {}
    for t in range(BACKFILL_BARS + 3):
        result = detector.step(bar(t, -10.0 + t * 0.01))
        if result:
            events[t] = result
        if t == BACKFILL_BARS - 2:
            assert detector.warming
    first = min(events)
    assert first == BACKFILL_BARS + 1  # 0-based: bars 400 and 401 are the two shrink steps
    assert events[first][0].window.started_at == (BACKFILL_BARS - 1) * HOUR


def test_duplicate_and_out_of_order_bars_are_ignored():
    detector, _ = feed(hists(-1.0, -0.8, -0.6))
    following_before = detector.following
    contracting_before = detector.contracting.window

    assert detector.step(bar(2, -0.6)) == []  # duplicate
    assert detector.step(bar(1, -0.1)) == []  # out of order
    assert detector.contracting.window == contracting_before
    assert detector.following == following_before
    # And the run carries on as if those never arrived.
    assert [(e.kind, e.window.bars) for e in detector.step(bar(3, -0.4))] == [("updated", 3)]


def test_a_warm_bar_without_bands_fails_loudly():
    detector, _ = feed(hists(-1.0))
    with pytest.raises(ValueError, match="no bands"):
        detector.step(bar(1, -0.8, bands=None))


def test_xyz_symbols_carry_their_asset_class():
    _, events = feed(hists(-1.0, -0.8, -0.6), symbol="xyz:GOLD")
    window = events[2][0].window
    assert window.symbol == "xyz:GOLD"
    assert window.asset_class == "commodity"
