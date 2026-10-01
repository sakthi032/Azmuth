"""Azmuth 2.0 entry point.

Usage:

* ``py main.py``      — bootstrap check; prints a status summary.
* ``py main.py --ui`` — open the Azmuth desktop window (real brain).

CMD/PowerShell remains a development tool. The desktop window is the
normal interface.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Sequence

from app import __phase__, __version__
from app.config.logging_setup import get_logger, setup_logging
from app.config.settings import Settings, load_settings
from app.core.errors import AzmuthError


def bootstrap() -> Settings:
    """Load configuration, prepare directories and start logging."""
    settings = load_settings()
    settings.paths.ensure()
    setup_logging(settings)
    return settings


def _status_lines(settings: Settings) -> list[str]:
    return [
        f"AZMUTH {__version__} ({__phase__})",
        "",
        f"ENVIRONMENT     {settings.env}",
        f"LOG LEVEL       {settings.log_level}",
        f"LANGUAGES       {', '.join(settings.supported_languages)}"
        f" (default: {settings.default_language})",
        f"DATA ROOT       {settings.paths.data}",
        f"LLM PROVIDER    {settings.llm_provider}",
        f"SEARCH PROVIDER {settings.search_provider}",
        "",
        "All Phase 1 steps implemented.",
        "",
        "Run `py main.py --ui` to open the Azmuth window.",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    try:
        settings = bootstrap()
    except AzmuthError as exc:
        print(f"Azmuth failed to start: {exc}", file=sys.stderr)
        return 2

    logger = get_logger("bootstrap")
    logger.info(
        "Azmuth bootstrap complete",
        extra={"event": "SYSTEM_STARTED", "env": settings.env},
    )

    if "--ui" in args:
        return _run_ui(settings)
    if "--chat" in args:
        return _run_chat(settings)

    print("\n".join(_status_lines(settings)))
    return 0


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
def _run_ui(settings: Settings) -> int:
    from app.brain import build_azmuth
    from app.language import build_language_manager
    from app.ui.app import AzmuthDesktopApp
    from app.ui.bridge import UIController

    language = build_language_manager(settings)
    azmuth = build_azmuth(settings)
    asyncio.run(azmuth.startup())

    # respond_as_text already matches the Responder protocol:
    #     (UserInput, str | None) -> Awaitable[tuple[str, str]]
    controller = UIController(language=language, responder=azmuth.respond_as_text)

    try:
        app = AzmuthDesktopApp(controller)
        app.run()
    except RuntimeError as exc:
        print(f"Could not open the desktop UI: {exc}", file=sys.stderr)
        return 3
    finally:
        try:
            asyncio.run(azmuth.shutdown())
        except Exception:  # noqa: BLE001
            pass
    return 0


# --------------------------------------------------------------------------
# Chat (developer fallback; not the intended interface)
# --------------------------------------------------------------------------
def _run_chat(settings: Settings) -> int:
    from app.brain import build_azmuth
    from app.language import build_language_manager
    from app.voice import InputManager

    language = build_language_manager(settings)
    inputs = InputManager(language)
    azmuth = build_azmuth(settings)

    async def _chat() -> None:
        await azmuth.startup()
        try:
            print("Azmuth chat — type 'quit' to exit.")
            while True:
                try:
                    text = input("you> ").strip()
                except EOFError:
                    break
                if not text or text.lower() in {"quit", "exit"}:
                    break
                user_input = inputs.from_text(text)
                response = await azmuth.respond(user_input)
                print(f"azmuth> {response.text}")
        finally:
            await azmuth.shutdown()

    asyncio.run(_chat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())