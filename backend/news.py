import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional
import aiohttp
from google import genai
from google.genai import types
from backend.cmc_client import CmcClient

logger = logging.getLogger(__name__)
# Every text-flash model on the key that supports search grounding, best-first
# (capability order; lites later). Each has its own free-tier quota bucket, so on
# 503 overload / 429 quota the chain walks to the next bucket. Verified live via
# models.list() + grounded test calls. Last resort is neutral, never a blocked trade.
_GROUNDED_MODELS = [
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
    "gemini-flash-lite-latest",
]
# Cache catalyst verdicts per coin: the prompt looks at a 48h news window, so
# re-asking the same coin every hourly scan only burns free-tier quota (~250
# requests/day) for the same answer.
_CATALYST_CACHE_TTL = 6 * 3600  # seconds
_CATALYST_PROMPT = """Search the web for news about {name} ({symbol}) cryptocurrency. \
Consider ONLY news published in the last 48 hours; ignore anything older. \
Ignore generic price-prediction, forecast, or technical-analysis articles — only \
concrete events count (exchange listing, partnership, product launch, hack, \
regulatory action, token migration/rebrand).
Classify the single most important recent catalyst and judge sentiment for the next 24 hours.
Reply in EXACTLY this format and nothing else:
LATEST_NEWS_DATE: <date of the most recent item in YYYY-MM-DD form, or NONE>
CATALYST: <one of: listing, partnership, launch, migration, none>
SENTIMENT: <integer 0-100, 50=neutral; if no qualifying news in the last 48h, output 50>
REASON: <one short sentence>"""
_VALID_CATALYSTS = {"listing", "partnership", "launch", "migration", "none"}


@dataclass
class NewsResult:
    score: float
    explanation: str
    analyzed: bool = True  # False = no real news (fallback); caller should ignore the score


@dataclass
class CatalystResult:
    sentiment: float      # 0-100, 50 = neutral
    catalyst: str         # none | listing | partnership | launch | migration
    latest_date: str      # date string, or "NONE"
    reason: str
    analyzed: bool = True  # False = no recent news / lookup failed -> sentiment is neutral filler


_NEUTRAL_CATALYST = CatalystResult(50.0, "none", "NONE", "No recent news.", analyzed=False)


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
        self._catalyst_cache: dict[str, tuple[float, CatalystResult]] = {}

    def grounded_catalyst(self, symbol: str, name: str) -> CatalystResult:
        """One web-search-grounded Gemini call: recent (48h) news catalyst + sentiment.
        Used as the pre-trade gate. Verdicts are cached per coin for a few hours
        (quota budget); on failure it falls back through alternate models (503/429
        usually hit one model's bucket, not all), then fails safe to neutral so an
        API hiccup never blocks a trade. Called only for candidates about to open."""
        cached = self._catalyst_cache.get(symbol)
        if cached and time.monotonic() - cached[0] < _CATALYST_CACHE_TTL:
            return cached[1]
        last_err: Exception | None = None
        for model in _GROUNDED_MODELS:
            try:
                resp = self._client.models.generate_content(
                    model=model,
                    contents=_CATALYST_PROMPT.format(name=name or symbol, symbol=symbol),
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())]
                    ),
                )
                result = self._parse_catalyst(resp.text or "")
                self._catalyst_cache[symbol] = (time.monotonic(), result)
                return result
            except Exception as e:
                last_err = e
                logger.debug("  %s: %s failed (%s) — trying next model", symbol, model, e)
        # Failures are NOT cached — quota/overload may clear by the next scan.
        logger.warning("  %s: all grounded models failed (%s) — neutral", symbol, last_err)
        return _NEUTRAL_CATALYST

    @staticmethod
    def _parse_catalyst(text: str, max_age_hours: float = 72.0) -> CatalystResult:
        def grab(key: str, default: str) -> str:
            m = re.search(rf"{key}\s*:\s*(.+)", text, re.IGNORECASE)
            return m.group(1).strip() if m else default

        date = grab("LATEST_NEWS_DATE", "NONE")
        catalyst = grab("CATALYST", "none").lower().split()[0] if grab("CATALYST", "none") else "none"
        if catalyst not in _VALID_CATALYSTS:
            catalyst = "none"
        m = re.search(r"\d+", grab("SENTIMENT", "50"))
        sentiment = max(0.0, min(100.0, float(m.group()))) if m else 50.0
        analyzed = date.strip().upper() != "NONE"

        # Don't TRUST the model's recency claim — verify it. Stale articles (the
        # 3-week-old MAT pattern) get downgraded to neutral instead of moving trades.
        if analyzed:
            try:
                import pandas as pd
                parsed = pd.to_datetime(date, errors="coerce", utc=True)
                if parsed is not pd.NaT:
                    age_h = (pd.Timestamp.now(tz="UTC") - parsed).total_seconds() / 3600
                    if age_h > max_age_hours:
                        return CatalystResult(50.0, "none", date,
                                              f"Newest item is {age_h / 24:.0f}d old — stale, ignored.",
                                              analyzed=False)
            except Exception:
                pass  # unparseable date: keep the model's verdict
        return CatalystResult(sentiment, catalyst, date, grab("REASON", ""), analyzed)

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
