
"""Demonstrate purpose-based routing with real Ollama models.

Sends four different questions through the pipeline and shows which
model answered each one.

Run:  py -m scripts.routing_demo
"""

from __future__ import annotations

import asyncio
import time

from app.brain import build_azmuth
from app.config.settings import load_settings
from app.voice import InputManager


QUESTIONS = [
    ("chat",      "Say hi in a warm, friendly way."),
    ("coding",    "Write a one-line Python function that adds two numbers."),
    ("reasoning", "Why does ice float on water?"),
    ("fast",      "What is 2 + 2?"),
]


async def main() -> None:
    settings = load_settings()
    azmuth = build_azmuth(settings)
    await azmuth.startup()

    try:
        inputs = InputManager(azmuth.language)

        for label, question in QUESTIONS:
            print(f"\n=== {label.upper()} ===")
            print(f"Q: {question}")
            started = time.perf_counter()
            response = await azmuth.respond(inputs.from_text(question))
            elapsed = (time.perf_counter() - started) * 1000
            print(f"A: {response.text[:200]}")
            print(f"   purpose={response.purpose}  lang={response.language}  ({elapsed:.0f} ms)")

    finally:
        await azmuth.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
