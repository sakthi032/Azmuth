
"""Groq provider - OpenAI-compatible endpoint at api.groq.com."""

from app.llm.providers.openai_compatible import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"

    def __init__(self, **kwargs):
        kwargs.setdefault("base_url", "https://api.groq.com/openai/v1")
        super().__init__(**kwargs)

    def is_available(self) -> bool:
        """Config-only check - no network probe.

        A provider with a base URL, a model, and a key is treated as
        available. If the key turns out to be wrong, the request fails
        and the pipeline's fallback handles it. This keeps routing fast
        (no per-request HTTP) and avoids the case where a transient
        network hiccup silently disables an otherwise working provider.
        """
        return bool(self._base_url) and bool(self._model) and bool(self._api_key)
