"""Tests for the SearXNG provider."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.errors import ProviderError
from app.search.models import SearchQuery
from app.search.providers.searxng import SearXNGProvider

_MOCK = {
    "number_of_results": 2,
    "results": [
        {
            "title": "Gold Rate Chennai",
            "url": "https://example.com/gold",
            "content": "22K gold is  ₹6,450/gram today.",
            "engine": "google",
        },
        {
            "title": "Live Gold",
            "url": "https://example.com/live",
            "content": "Spot price update.",
            "engine": "bing",
        },
    ],
}


@pytest.mark.asyncio
async def test_parses_results_and_normalizes_whitespace():
    provider = SearXNGProvider(base_url="http://localhost:8888")

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
        resp = MagicMock(status_code=200)
        resp.json.return_value = _MOCK
        get.return_value = resp

        out = await provider.search(SearchQuery(text="gold rate chennai"))

    assert out.provider == "searxng"
    assert len(out.results) == 2
    assert out.results[0].title == "Gold Rate Chennai"
    assert out.results[0].source == "google"
    # newlines collapsed
    assert "\n" not in (out.results[0].snippet or "")


@pytest.mark.asyncio
async def test_403_raises_actionable_error():
    provider = SearXNGProvider(base_url="http://localhost:8888")

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
        get.return_value = MagicMock(status_code=403)

        with pytest.raises(ProviderError, match="JSON format"):
            await provider.search(SearchQuery(text="x"))


@pytest.mark.asyncio
async def test_empty_results_is_not_an_exception():
    provider = SearXNGProvider(base_url="http://localhost:8888")

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"results": []}
        get.return_value = resp

        out = await provider.search(SearchQuery(text="obscure"))
        assert out.results == []