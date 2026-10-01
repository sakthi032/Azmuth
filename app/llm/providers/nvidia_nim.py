
"""NVIDIA NIM provider - OpenAI-compatible endpoint at integrate.api.nvidia.com."""

from app.llm.providers.openai_compatible import OpenAICompatibleProvider


class NvidiaNIMProvider(OpenAICompatibleProvider):
    name = "nvidia_nim"

    def __init__(self, **kwargs):
        kwargs.setdefault(
            "base_url", "https://integrate.api.nvidia.com/v1"
        )
        super().__init__(**kwargs)

    def is_available(self) -> bool:
        return bool(self._base_url) and bool(self._model) and bool(self._api_key)
