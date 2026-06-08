import json
import logging
import re
from dataclasses import dataclass
from typing import Optional
import aiohttp
from google import genai
from backend.cmc_client import CmcClient

logger = logging.getLogger(__name__)


@dataclass
class NewsResult:
    score: float
    explanation: str
    analyzed: bool = True  # False = no real news (fallback); caller should ignore the score


_CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/v2/news/"
_NEUTRAL = NewsResult(score=50.0, explanation="News analysis unavailable.", analyzed=False)
_NO_NEWS = NewsResult(score=50.0, explanation="No recent news found.", analyzed=False)
_MODEL = "gemini-3.5-flash"
_PROMPT = """You are a crypto market analyst. Given these news headlines about {name} ({symbol}), \
judge how bullish or bearish the news is for the coin's price over the next 24 hours.

Return a JSON object with:
- "score": integer 0-100 (0 = very bearish, 50 = neutral, 100 = very bullish)
- "explanation": one sentence naming the key sentiment driver

Headlines:
{headlines}

Return ONLY valid JSON, no markdown, no extra text."""


class NewsClient:
    def __init__(self, gemini_api_key: str, cmc_client: Optional[CmcClient] = None):
        self._client = genai.Client(api_key=gemini_api_key)
        self._cmc = cmc_client

    async def _fetch_cryptocompare(self, symbol: str, limit: int) -> list[str]:
        params = {"categories": symbol, "lang": "EN", "sortOrder": "latest", "limit": limit}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(_CRYPTOCOMPARE_URL, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            return [item["title"] for item in data.get("Data", [])[:limit]]
        except Exception:
            return []

    async def fetch_headlines(self, symbol: str, limit: int = 5) -> list[str]:
        """
        Fetch headlines for a coin. Tries CMC's native news endpoint first,
        then falls back to CryptoCompare for wider coverage.
        """
        headlines: list[str] = []
        if self._cmc is not None:
            headlines = await self._cmc.fetch_news(symbol, limit=limit)
        if not headlines:
            headlines = await self._fetch_cryptocompare(symbol, limit=limit)
        return headlines

    def analyze_sentiment(self, symbol: str, name: str, headlines: list[str]) -> NewsResult:
        """
        Let Gemini read the headlines and decide a 0-100 sentiment score plus a
        one-line explanation. Returns a neutral fallback on any failure.
        """
        if not headlines:
            logger.debug("  %s: no headlines found — neutral news (Gemini not called)", symbol)
            return _NO_NEWS
        prompt = _PROMPT.format(
            name=name,
            symbol=symbol,
            headlines="\n".join(f"- {h}" for h in headlines),
        )
        try:
            response = self._client.models.generate_content(model=_MODEL, contents=prompt)
            text = (response.text or "").strip()
            text = re.sub(r"```(?:json)?", "", text).strip()
            parsed = json.loads(text)
            result = NewsResult(
                score=float(parsed["score"]),
                explanation=str(parsed["explanation"]),
            )
            logger.debug("  %s: Gemini news score=%.0f — %s",
                         symbol, result.score, result.explanation)
            return result
        except Exception as e:
            # Surface the real reason (e.g. an invalid model id 404s here) instead
            # of silently scoring every coin a neutral 50.
            logger.warning("  %s: Gemini analysis failed (%s) — neutral fallback", symbol, e)
            return _NEUTRAL
