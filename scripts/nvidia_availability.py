
"""Test which NVIDIA NIM models this account can actually call.

Run:  py -m scripts.nvidia_availability
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.config.settings import load_settings


# Candidates grouped by intended purpose. The script tests every one;
# the results tell you which to keep in the registry.
CANDIDATES = [
    # Already known to work
    "nvidia/nemotron-3-super-120b-a12b",
    "openai/gpt-oss-20b",
    # Chat / reasoning alternatives
    "z-ai/glm-5.3",
    "z-ai/glm-5.3-flash",
    "moonshotai/kimi-k3",
    "moonshotai/kimi-k2.6",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "mistralai/mistral-large-2-instruct",
    # Coding alternatives
    "mistralai/codestral-22b-instruct-v0.1",
    "deepseek-ai/deepseek-v4.1-flash",
    "deepseek-ai/deepseek-coder-6.7b-instruct",
    "meta/codellama-70b",
    "ibm/granite-34b-code-instruct",
    "ibm/granite-8b-code-instruct",
    "bigcode/starcoder2-15b",
    "qwen/qwen2.5-coder-32b-instruct",
    "qwen/qwen3-coder-480b-a35b-instruct",
]


def probe(model: str, *, api_key: str, base_url: str) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "curl/8.5.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=500.0) as resp:
            return "OK"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:110]
        return f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def main() -> None:
    s = load_settings()
    if not s.nvidia_api_key:
        print("NVIDIA_API_KEY is not set")
        return
    print(f"{'model':48} status")
    print("-" * 90)
    working: list[str] = []
    for model in CANDIDATES:
        status = probe(model, api_key=s.nvidia_api_key, base_url=s.nvidia_base_url)
        print(f"{model:48} {status}")
        if status == "OK":
            working.append(model)
    print()
    print(f"Working models ({len(working)}):")
    for m in working:
        print(f"  {m}")


if __name__ == "__main__":
    main()

# py -m scripts.nvidia_availability