"""SymbolState: one symbol's indicators and detector behind `on_bar()` and `peek()`."""

import copy

from live_macd_searcher.detect.config import BACKFILL_BARS
from live_macd_searcher.detect.normalise import pct_of_close
from live_macd_searcher.detect.symbol_state import SymbolState
from tests.fakes import synthetic_history

HISTORY = synthetic_history(BACKFILL_BARS + 50, phase=0.0)


def warm_state() -> SymbolState:
    state = SymbolState("BTC")
    for candle in HISTORY[: BACKFILL_BARS + 20]:
        state.on_bar(candle)
    return state


def indicator_values(state: SymbolState):
    return (state.macd.fast.value, state.macd.slow.value, state.macd.signal.value,
            list(state.bands.closes))  # fmt: skip


def test_a_duplicate_or_out_of_order_bar_leaves_the_indicators_untouched():
    # The detector ignores such bars too, but an EMA can't un-see one: it must never
    # reach the indicators at all.
    state = warm_state()
    before = indicator_values(state)
    assert state.on_bar(HISTORY[BACKFILL_BARS + 19]) == []  # duplicate
    assert state.on_bar(HISTORY[5]) == []  # out of order
    assert indicator_values(state) == before


def test_peek_is_none_while_warming():
    state = SymbolState("BTC")
    for candle in HISTORY[: BACKFILL_BARS - 1]:
        state.on_bar(candle)
    assert state.warming
    assert state.peek(HISTORY[BACKFILL_BARS - 1]) is None


def test_peek_reads_the_forming_bar_without_committing_it():
    state = warm_state()
    before = indicator_values(state)
    forming = HISTORY[BACKFILL_BARS + 20]

    reading = state.peek(forming)

    assert indicator_values(state) == before
    assert state.last_bar == HISTORY[BACKFILL_BARS + 19]
    # It reads exactly what committing that bar would compute.
    point = copy.deepcopy(state.macd).update(forming.close)
    assert reading.hist_pct == pct_of_close(point.hist, forming.close)
    assert reading.open_time == forming.open_time
