from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketState:
    """Scanner → API shared view of the BTC regime, so the dashboard can say WHY
    the board is empty (whales pause in a bear regime) instead of a silent zero.
    One process, one scanner — a module singleton, like SCAN_CLOCK."""
    regime_bullish: Optional[bool] = None  # None until the first check completes
    whales_blocked: int = 0                # whale spikes skipped in the current bear stretch


MARKET_STATE = MarketState()
