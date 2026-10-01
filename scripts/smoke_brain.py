"""End-to-end smoke test for the wired brain.

Run:  py -m scripts.smoke_brain
"""

import asyncio

from app.brain import build_azmuth
from app.config.settings import load_settings
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.voice import InputManager


async def main() -> None:
    settings = load_settings()
    azmuth = build_azmuth(settings)
    await azmuth.startup()
    try:
        inputs = InputManager(azmuth.language)

        print("=== Status ===")
        for name, value in azmuth.status().as_dict().items():
            print(f"  {name:12} {value}")

        print()
        print("=== Tools exposed to the LLM ===")
        for md in azmuth.tools.list_metadata():
            print(f"  {md.name}: {md.description}")

        print()
        print("=== Turns ===")
        for text in [
            "hello there",
            "What's the weather today?",
            "இன்று வானிலை என்ன?",
            "Azmuth, Chrome open pannu",
        ]:
            user_input = inputs.from_text(text)
            response = await azmuth.respond(user_input)
            print(f"  user:   {text!r}")
            print(f"  azmuth: {response.text!r}")
            print(f"          lang={response.language} "
                  f"search={response.used_search} "
                  f"memory={response.used_memory} "
                  f"iters={response.iterations}")
            print()
    finally:
        await azmuth.shutdown()


if __name__ == "__main__":
    asyncio.run(main())