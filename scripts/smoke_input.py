
"""Manual smoke test for the raw input layer.

Run from a fresh PowerShell (not from inside pytest). Sends real
keystrokes and mouse moves, so it is a manual test by design.

    py -m scripts.smoke_input
"""

import asyncio

from app.automation.input import InputController


async def main() -> None:
    c = InputController()
    if not c.is_available():
        print("input controller not available on this platform")
        return

    print("screen size:", await c.screen_size())
    print("cursor position:", await c.position())

    print("\nSending Win+I to open Settings...")
    await c.press_keys("win+i")
    await asyncio.sleep(2)

    print("Sending Alt+F4 to close it...")
    await c.press_keys("alt+f4")

    print("\nIf Settings opened and closed, the input layer works.")


if __name__ == "__main__":
    asyncio.run(main())