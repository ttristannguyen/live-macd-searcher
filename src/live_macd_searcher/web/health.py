"""Judging the feed's health: the one place the wall clock meets the data (invariant 2).

Honest by construction: anything short of a live, recent feed is reported as such, with
the reasons in plain words, and the board carries the same status (PLAN M6).
"""

from ..detect.config import BAR_STALE_HOURS, FEED_STALE_SECONDS
from ..ingest.feed import HOUR_MS
from ..runtime import RuntimeStatus
from .models import Health


def judge(
    status: RuntimeStatus, failure: str | None, newest_bar: int | None, now: float
) -> Health:
    """`now` and the status times are wall-clock seconds; `newest_bar` is an open_time."""

    def age(when: float | None) -> float | None:
        return None if when is None else now - when

    message_age = age(status.last_message_at)
    refresh_age = age(status.last_refresh_at)
    # Age since the newest bar *closed*, an hour after it opened.
    bar_age = age(None if newest_bar is None else (newest_bar + HOUR_MS) / 1000)

    reasons: list[str] = []
    if failure is not None:
        state, reasons = "failed", [f"the detector stopped: {failure}"]
    elif not status.streamed:
        state, reasons = "starting", ["warming up: the first gap-fill hasn't finished"]
    else:
        if not status.streaming:
            reasons.append("reconnecting to the feed")
        if message_age is not None and message_age > FEED_STALE_SECONDS:
            reasons.append(f"no websocket message for {message_age:.0f}s")
        if bar_age is not None and bar_age > BAR_STALE_HOURS * 3600:
            reasons.append(f"the newest closed bar closed {bar_age / 3600:.1f}h ago")
        state = "stale" if reasons else "ok"

    return Health(
        status=state,
        reasons=reasons,
        streaming=status.streaming,
        last_message_age_s=message_age,
        last_refresh_age_s=refresh_age,
        newest_bar_age_s=bar_age,
        symbols=status.symbols,
        warm=status.warm,
        warming=status.symbols - status.warm,
        rest_429s=status.rest_429s,
        rest_weight_spent=status.rest_weight_spent,
    )
