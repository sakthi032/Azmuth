
"""Print Azmuth's model routing table.

Run:  py -m scripts.routing_probe
"""

from __future__ import annotations

from app.config.settings import load_settings
from app.llm import LLMRouter, ModelPurpose, build_default_registry


def main() -> None:
    settings = load_settings()
    router = LLMRouter(settings, build_default_registry())

    print("=== LLM ROUTING TABLE ===")
    print()
    for row in router.describe_routing():
        purpose = row["purpose"]
        selected = row["selected"] or "(none available)"
        provider = row["provider"] or "-"
        candidates = row["candidates"]
        print(f"  {purpose:12} -> {selected}")
        print(f"                 provider={provider}")
        if candidates:
            print(f"                 candidates: {', '.join(candidates)}")
        print()

    print("=== OLLAMA MODELS VISIBLE ===")
    from app.llm.providers.ollama import OllamaProvider
    probe = OllamaProvider(model="qwen2.5-coder:7b", base_url=settings.ollama_base_url)
    installed = probe._list_installed_models()
    if installed is None:
        print("  Ollama daemon is not reachable at", settings.ollama_base_url)
    elif not installed:
        print("  Ollama daemon responded, but no models are pulled")
    else:
        for name in sorted(installed):
            print(f"  {name}")

    print()
    print("=== NVIDIA NIM ===")
    if settings.nvidia_api_key:
        print(f"  API key configured (…{settings.nvidia_api_key[-6:]})")
        print(f"  Base URL: {settings.nvidia_base_url}")
    else:
        print("  No API key set — NVIDIA tier will be skipped by the router")


if __name__ == "__main__":
    main()
