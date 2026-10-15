"""Verbose diagnostic: cursor position, focus, and paste result."""
from __future__ import annotations
import asyncio
import ctypes
from ctypes import wintypes


async def main() -> None:
    from app.automation.input import InputController, _KEYEVENTF_KEYUP
    from app.automation.uia import UIAutomationController
    from app.automation.windows import WindowsAutomationImpl

    uia = UIAutomationController()
    auto = WindowsAutomationImpl()
    inp = InputController()

    user32 = ctypes.windll.user32
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetFocus.restype = wintypes.HWND

    def cursor():
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)

    def fg():
        return int(user32.GetForegroundWindow() or 0)

    def focus():
        return int(user32.GetFocus() or 0)

    print(f"screen: {user32.GetSystemMetrics(0)} x {user32.GetSystemMetrics(1)}")
    print(f"cursor initially: {cursor()}")
    print()

    await auto.open_application("notepad")
    await asyncio.sleep(2.5)

    windows = await auto.list_windows()
    notes = [w for w in windows if "notepad" in (w.title or "").lower()]
    if not notes:
        print("no notepad launched"); return
    target = notes[0]
    print(f"target hwnd={target.handle}  title={target.title!r}")

    await auto.focus_window(str(target.handle))
    await asyncio.sleep(0.5)
    print(f"after focus: fg={fg()}  match={fg() == target.handle}")
    print()

    tree = await uia.get_window_tree(window=target.title, interactive_only=False)
    edits = [e for e in tree["elements"]
             if "document" in (e["control_type"] or "").lower()]
    if not edits:
        print("no edit element"); return
    rect = edits[0].get("rect")
    print(f"edit rect: {rect}")

    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    print(f"click target: ({cx}, {cy})")
    print(f"cursor BEFORE click: {cursor()}")
    print(f"fg     BEFORE click: {fg()}")
    print(f"focus  BEFORE click: {focus()}")

    await inp.click(cx, cy)
    await asyncio.sleep(0.5)

    print()
    print(f"cursor AFTER click: {cursor()}")
    print(f"fg     AFTER click: {fg()}  match={fg() == target.handle}")
    print(f"focus  AFTER click: {focus()}")
    print()

    print("writing clipboard...")
    inp._write_clipboard("PROBE-TEXT-67890")
    print(f"fg={fg()}  focus={focus()}")
    print("sending ctrl+v...")

    VK_CONTROL = 0x11
    VK_V = 0x56
    inp._send(inp._key_input(VK_CONTROL))
    await asyncio.sleep(0.05)
    inp._send(inp._key_input(VK_V))
    await asyncio.sleep(0.05)
    inp._send(inp._key_input(VK_V, _KEYEVENTF_KEYUP))
    await asyncio.sleep(0.05)
    inp._send(inp._key_input(VK_CONTROL, _KEYEVENTF_KEYUP))
    await asyncio.sleep(1.0)

    windows = await auto.list_windows()
    notes = [w for w in windows if "notepad" in (w.title or "").lower()]
    for n in notes:
        print(f"notepad title now: {n.title!r}")


if __name__ == "__main__":
    asyncio.run(main())