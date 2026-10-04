"""The app's vocabulary as types (CLAUDE.md). One definition, used from detector to API.

Because these are `Literal`s, the web layer can declare query parameters with them and
FastAPI rejects anything else before it reaches SQL.
"""

from typing import Literal

Side = Literal["bullish", "bearish"]
Regime = Literal["reversal", "continuation", "transition"]
Band = Literal["far", "near", "through"]
State = Literal["active", "crossed", "failed", "hit", "reversed", "expired"]
AssetClass = Literal["crypto", "equity", "index", "commodity", "fx"]
EventKind = Literal["opened", "updated", "crossed", "resolved"]

LIVE_STATES: frozenset[State] = frozenset({"active", "crossed"})
