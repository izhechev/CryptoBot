from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from backend.config import Config
from backend.storage import Storage, Signal


@dataclass
class SignalEvent:
    coin_symbol: str
    coin_name: str
    total_score: float
    technical_score: float
    news_score: float
    gemini_explanation: str
    signal_id: int


class SignalEngine:
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db

    def evaluate(
        self,
        coin_symbol: str,
        coin_name: str,
        total_score: float,
        technical_score: float,
        news_score: float,
        gemini_explanation: str,
    ) -> Optional[SignalEvent]:
        """Return a SignalEvent if the score passes threshold and no open position exists."""
        if total_score < self._cfg.signal_threshold:
            return None
        if self._db.has_open_position(coin_symbol):
            return None

        saved = self._db.save_signal(Signal(
            id=None,
            coin_symbol=coin_symbol,
            coin_name=coin_name,
            total_score=total_score,
            technical_score=technical_score,
            news_score=news_score,
            gemini_explanation=gemini_explanation,
            fired_at=datetime.now(timezone.utc),
        ))

        return SignalEvent(
            coin_symbol=coin_symbol,
            coin_name=coin_name,
            total_score=total_score,
            technical_score=technical_score,
            news_score=news_score,
            gemini_explanation=gemini_explanation,
            signal_id=saved.id,
        )
