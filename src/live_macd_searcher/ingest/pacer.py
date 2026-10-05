"""The REST pacer: every Hyperliquid REST call spends weight here first.

Hyperliquid's REST budget is per IP, and this droplet's IP is shared with
`macd_searcher` (DESIGN §5). Capping our own spending at `REST_WEIGHT_PER_MIN` — half
the limit — is what guarantees this app can never starve it.
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

WINDOW_SECONDS = 60.0


class RestPacer:
    """A sliding one-minute budget of request weight. Callers wait their turn, in order."""

    def __init__(
        self,
        weight_per_min: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.weight_per_min = weight_per_min
        self._clock = clock
        self._sleep = sleep
        self._spent: deque[tuple[float, int]] = deque()  # (when, weight), oldest first
        self._lock = asyncio.Lock()

    async def spend(self, weight: int) -> None:
        """Wait until `weight` fits in the last minute's budget, then record it as spent."""
        if weight > self.weight_per_min:
            raise ValueError(f"weight {weight} exceeds the whole budget {self.weight_per_min}")
        # One caller at a time, so waiters are served in arrival order.
        async with self._lock:
            while True:
                now = self._clock()
                while self._spent and self._spent[0][0] <= now - WINDOW_SECONDS:
                    self._spent.popleft()
                if sum(w for _, w in self._spent) + weight <= self.weight_per_min:
                    self._spent.append((now, weight))
                    return
                # Sleep until the oldest spend leaves the window, then look again.
                await self._sleep(self._spent[0][0] + WINDOW_SECONDS - now)
