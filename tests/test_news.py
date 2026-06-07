import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.news import NewsClient, NewsResult


@pytest.fixture
def news_client(cfg):
    with patch("backend.news.genai.Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        client = NewsClient(cfg.gemini_api_key)
    return client


@pytest.mark.asyncio
async def test_fetch_headlines_prefers_cmc(cfg):
    """CMC is the primary source; CryptoCompare is only used as fallback."""
    mock_cmc = MagicMock()
    mock_cmc.fetch_news = AsyncMock(return_value=["CMC headline 1", "CMC headline 2"])

    with patch("backend.news.genai.Client", return_value=MagicMock()):
        client = NewsClient(cfg.gemini_api_key, cmc_client=mock_cmc)

    cc_called = False

    async def fake_cc(symbol, limit):
        nonlocal cc_called
        cc_called = True
        return ["should not be used"]

    client._fetch_cryptocompare = fake_cc
    headlines = await client.fetch_headlines("BTC")

    assert headlines == ["CMC headline 1", "CMC headline 2"]
    assert cc_called is False


@pytest.mark.asyncio
async def test_fetch_headlines_falls_back_to_cryptocompare(cfg):
    """When CMC returns nothing, fall back to CryptoCompare."""
    mock_cmc = MagicMock()
    mock_cmc.fetch_news = AsyncMock(return_value=[])

    with patch("backend.news.genai.Client", return_value=MagicMock()):
        client = NewsClient(cfg.gemini_api_key, cmc_client=mock_cmc)

    async def fake_cc(symbol, limit):
        return ["CC fallback headline"]

    client._fetch_cryptocompare = fake_cc
    headlines = await client.fetch_headlines("BTC")
    assert headlines == ["CC fallback headline"]


def test_analyze_sentiment_calls_gemini(news_client):
    mock_models = MagicMock()
    mock_models.generate_content.return_value = MagicMock(
        text='{"score": 78, "explanation": "Strong positive news."}'
    )
    news_client._client.models = mock_models

    result = news_client.analyze_sentiment(
        "BTC", "Bitcoin", ["Bitcoin surges", "BTC ETF approved"]
    )
    assert isinstance(result, NewsResult)
    assert result.score == 78.0
    assert "positive" in result.explanation


def test_analyze_sentiment_strips_markdown_fences(news_client):
    mock_models = MagicMock()
    mock_models.generate_content.return_value = MagicMock(
        text='```json\n{"score": 90, "explanation": "Bullish."}\n```'
    )
    news_client._client.models = mock_models

    result = news_client.analyze_sentiment("ETH", "Ethereum", ["good news"])
    assert result.score == 90.0


def test_analyze_sentiment_fallback_on_bad_json(news_client):
    mock_models = MagicMock()
    mock_models.generate_content.return_value = MagicMock(text="not valid json")
    news_client._client.models = mock_models

    result = news_client.analyze_sentiment("BTC", "Bitcoin", ["some news"])
    assert result.score == 50.0
    assert result.explanation == "News analysis unavailable."


def test_analyze_sentiment_fallback_on_empty_headlines(news_client):
    result = news_client.analyze_sentiment("BTC", "Bitcoin", [])
    assert result.score == 50.0
    assert result.explanation == "No recent news found."
