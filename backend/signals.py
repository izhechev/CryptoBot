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
    strategy: str = "standard"


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
        """Standard strategy: fire if score passes threshold and no open standard position."""
        if total_score < self._cfg.signal_threshold:
            return None
        if self._db.has_open_position(coin_symbol, strategy="standard"):
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
            strategy="standard",
        ))

        return SignalEvent(
            coin_symbol=coin_symbol,
            coin_name=coin_name,
            total_score=total_score,
            technical_score=technical_score,
            news_score=news_score,
            gemini_explanation=gemini_explanation,
            signal_id=saved.id,
            strategy="standard",
        )

    def emit_whale(
        self,
        coin_symbol: str,
        coin_name: str,
        volume_ratio: float,
        price_thrust_pct: float,
    ) -> Optional[SignalEvent]:
        """Whale strategy: rule-based (no score threshold). Deduped per-whale-position."""
        if self._db.has_open_position(coin_symbol, strategy="whale"):
            return None

        explanation = (
            f"Whale move: volume {volume_ratio:.1f}x average, "
            f"price +{price_thrust_pct:.1f}% surge."
        )
        saved = self._db.save_signal(Signal(
            id=None,
            coin_symbol=coin_symbol,
            coin_name=coin_name,
            total_score=100.0,
            technical_score=round(volume_ratio, 2),
            news_score=0.0,
            gemini_explanation=explanation,
            fired_at=datetime.now(timezone.utc),
            strategy="whale",
        ))

        return SignalEvent(
            coin_symbol=coin_symbol,
            coin_name=coin_name,
            total_score=100.0,
            technical_score=round(volume_ratio, 2),
            news_score=0.0,
            gemini_explanation=explanation,
            signal_id=saved.id,
            strategy="whale",
        )
