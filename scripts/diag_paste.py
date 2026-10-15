"""Diagnose why Ctrl+V isn't reaching Notepad's text field."""
from __future__ import annotations
import asyncio
import ctypes
from ctypes import wintypes

async def main() -> None:
    from app.automation.uia import UIAutomationController
    from app.automation.windows import WindowsAutomationImpl
    from app.automation.input import InputController

    uia = UIAutomationController()
    auto = WindowsAutomationImpl()
    inp = InputController()
    user32 = ctypes.windll.user32
    user32.GetFocus.argtypes = []
    user32.GetFocus.restype = wintypes.HWND

    print("launching notepad...")
    await auto.open_application("notepad")
    await asyncio.sleep(2.5)

    windows = await auto.list_windows()
    note = [w for w in windows if "notepad" in (w.title or "").lower()]
    if not note:
        print("no notepad")
        return
    target = note[0]
    print(f"target hwnd={target.handle} title={target.title!r}")

    # Focus it
    await auto.focus_window(str(target.handle))
    await asyncio.sleep(0.5)

    # Find the text editor element
    tree = await uia.get_window_tree(window=target.title, interactive_only=False)
    edits = [e for e in tree["elements"]
             if "document" in (e["control_type"] or "").lower()]
    if not edits:
        print("no edit element")
        return
    edit = edits[0]
    rect = edit.get("rect")
    print(f"edit rect: {rect}")
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2

    # ---- Report focus BEFORE click ----
    print()
    print("--- BEFORE click ---")
    print(f"  GetForegroundWindow = {inp._get_foreground_hwnd()}")
    print(f"  GetFocus (kbd focus) = {user32.GetFocus()}")
    print(f"  target hwnd          = {target.handle}")

    # Click into the text area
    await inp.click(cx, cy)
    await asyncio.sleep(0.4)

    # ---- Report focus AFTER click ----
    print()
    print("--- AFTER click ---")
    print(f"  GetForegroundWindow = {inp._get_foreground_hwnd()}")
    print(f"  GetFocus (kbd focus) = {user32.GetFocus()}")

    # ---- Report focus AFTER force_foreground ----
    inp._force_foreground(target.handle)
    await asyncio.sleep(0.2)
    print()
    print("--- AFTER _force_foreground ---")
    print(f"  GetForegroundWindow = {inp._get_foreground_hwnd()}")
    print(f"  GetFocus (kbd focus) = {user32.GetFocus()}")

    # Write to clipboard
    inp._write_clipboard("DIAGNOSTIC TEXT 12345")
    print()
    print("clipboard written")

    # Send Ctrl+V MANUALLY here (bypassing _paste_via_clipboard)
    VK_CONTROL = 0x11
    VK_V = 0x56
    from app.automation.input import _KEYEVENTF_KEYUP
    inp._send(inp._key_input(VK_CONTROL))
    await asyncio.sleep(0.03)
    inp._send(inp._key_input(VK_V))
    await asyncio.sleep(0.03)
    inp._send(inp._key_input(VK_V, _KEYEVENTF_KEYUP))
    await asyncio.sleep(0.03)
    inp._send(inp._key_input(VK_CONTROL, _KEYEVENTF_KEYUP))

    await asyncio.sleep(1.0)

    # Read the title to see if the doc changed
    windows = await auto.list_windows()
    note = [w for w in windows if "notepad" in (w.title or "").lower()]
    if note:
        print(f"after paste, title = {note[0].title!r}")


if __name__ == "__main__":
    asyncio.run(main())