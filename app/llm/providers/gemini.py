
"""Gemini provider - OpenAI-compatible endpoint at Google AI Studio."""

from app.llm.providers.openai_compatible import OpenAICompatibleProvider


class GeminiProvider(OpenAICompatibleProvider):
    name = "gemini"

    def __init__(self, **kwargs):
        kwargs.setdefault(
            "base_url", "https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        super().__init__(**kwargs)

    def is_available(self) -> bool:
        return bool(self._base_url) and bool(self._model) and bool(self._api_key)
