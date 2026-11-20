"""Scanner confidence levels describe the strength of current evidence."""
from datetime import datetime
from typing import Literal

import pandas as pd

from backend.config import Config
from backend.whale_strategy import WhaleSignal, measure_breakout


def classify_breakout(
    frame: pd.DataFrame, cfg: Config, *, as_of: datetime
) -> tuple[Literal["early", "confirmed"], WhaleSignal] | None:
    """An early heads-up includes quiet intervals in its volume comparison.

    Both levels require a close above the entire preceding day's high. The
    confirmed level also passes the paper entry rule's active-volume and
    turnover thresholds. Neither level estimates a future return.
    """
    evidence = measure_breakout(frame, cfg, as_of=as_of)
    if evidence is None:
        return None
    level = "confirmed" if evidence.confirmed(cfg) else "early"
    return level, evidence.as_signal()
