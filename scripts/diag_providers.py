"""Test each LLM provider directly and report status."""
from __future__ import annotations

import asyncio
import os

import httpx


async def test_nim() -> None:
    key = os.getenv("NVIDIA_API_KEY")
    print("=== NVIDIA NIM ===")
    if not key:
        print("  ⚠️  No NVIDIA_API_KEY in .env")
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "z-ai/glm-5.3",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 5,
                },
            )
        if r.status_code == 200:
            print(f"  ✅ OK (200) — {r.json()['choices'][0]['message']['content'][:40]!r}")
        else:
            print(f"  ❌ {r.status_code} — {r.text[:200]}")
    except Exception as exc:
        print(f"  ❌ Exception: {type(exc).__name__}: {exc}")


async def test_groq() -> None:
    key = os.getenv("GROQ_API_KEY")
    print("=== Groq ===")
    if not key:
        print("  ⚠️  No GROQ_API_KEY in .env")
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 5,
                },
            )
        if r.status_code == 200:
            print(f"  ✅ OK (200) — {r.json()['choices'][0]['message']['content'][:40]!r}")
        else:
            print(f"  ❌ {r.status_code} — {r.text[:200]}")
    except Exception as exc:
        print(f"  ❌ Exception: {type(exc).__name__}: {exc}")


async def test_gemini() -> None:
    key = os.getenv("GEMINI_API_KEY")
    print("=== Google Gemini ===")
    if not key:
        print("  ⚠️  No GEMINI_API_KEY in .env")
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={key}",
                json={
                    "contents": [{"parts": [{"text": "hi"}]}],
                    "generationConfig": {"maxOutputTokens": 5},
                },
            )
        if r.status_code == 200:
            print("  ✅ OK (200)")
        else:
            print(f"  ❌ {r.status_code} — {r.text[:200]}")
    except Exception as exc:
        print(f"  ❌ Exception: {type(exc).__name__}: {exc}")


async def test_ollama() -> None:
    print("=== Ollama ===")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://localhost:11434/api/tags")
        if r.status_code == 200:
            models = r.json().get("models", [])
            names = [m.get("name", "") for m in models]
            print(f"  ✅ Running — {len(names)} models: {names[:5]}")
            if not any("qwen" in n or "llama" in n for n in names):
                print(f"  ⚠️  No chat model found. Available: {names}")
        else:
            print(f"  ❌ {r.status_code}")
    except Exception as exc:
        print(f"  ❌ Not running: {type(exc).__name__}: {exc}")


async def main() -> None:
    # Load .env into os.environ
    from dotenv import load_dotenv
    load_dotenv()

    await test_nim()
    print()
    await test_groq()
    print()
    await test_gemini()
    print()
    await test_ollama()


if __name__ == "__main__":
    asyncio.run(main())