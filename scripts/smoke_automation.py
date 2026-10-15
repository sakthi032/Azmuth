"""Manual walk-through of the automation loop.

Run this while Azmuth is NOT running:
    py -m scripts.smoke_automation
"""

from __future__ import annotations

import asyncio
import json


async def main() -> None:
    from app.automation.input import InputController
    from app.automation.uia import UIAutomationController
    from app.automation.windows import WindowsAutomationImpl

    uia = UIAutomationController()
    auto = WindowsAutomationImpl()
    inp = InputController()

    print("=== Step 1: launch Notepad ===")
    result = await auto.open_application("notepad")
    print(json.dumps(result.as_dict(), indent=2))
    await asyncio.sleep(2.0)

    print("\n=== Step 2: find the Notepad window ===")
    await asyncio.sleep(1.0)
    windows = await auto.list_windows()
    candidates = [
        w for w in windows
        if "notepad" in (w.title or "").lower()
        and "*" not in (w.title or "")
    ]
    if not candidates:
        print("  FAIL: no fresh Notepad window found")
        return
    candidates.sort(key=lambda w: w.handle, reverse=True)
    target = candidates[0]
    print(f"  chose handle={target.handle}  title={target.title!r}")

    print("\n=== Step 3: focus it ===")
    ok = await auto.focus_window(str(target.handle))
    print(f"  focused={ok}")
    await asyncio.sleep(0.5)

    print("\n=== Step 4: observe the window ===")
    tree = await uia.get_window_tree(window=target.title, interactive_only=False)
    print(f"  window: {tree['window']!r}")
    print(f"  element count: {tree['count']}")

    print("\n=== Step 5: locate and click the text area ===")
    # Re-focus the window, then find the edit element.
    await auto.focus_window(str(target.handle))
    await asyncio.sleep(0.3)

    tree = await uia.get_window_tree(window=target.title, interactive_only=False)
    edit_targets = [
        e for e in tree["elements"]
        if any(k in (e.get("control_type") or "").lower()
               for k in ("edit", "document", "richtext"))
    ]
    print(f"  edit-type elements: {len(edit_targets)}")
    if not edit_targets:
        print("  FAIL: no edit-type element exposed.")
        return

    edit = edit_targets[0]
    rect = edit.get("rect")
    print(f"  targeting: {edit['name']!r}  rect={rect}")
    if not rect or len(rect) < 4:
        print("  FAIL: no bounding rect on edit element")
        return

    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    print(f"  clicking at ({cx}, {cy})")

    # Use SendInput-based click — this is what gives the RichEdit
    # keyboard focus. UIA's invoke_element falls back to mouse_event
    # which does not.
    await inp.click(cx, cy)
    await asyncio.sleep(0.4)

    print("\n=== Step 6: type into it ===")
    # Inline paste — this matches diag_probe.py, which is proven to work.
    # Bypasses type_text / _paste_via_clipboard entirely so we can confirm
    # the click+paste sequence works before re-enabling the wrapper.
    from app.automation.input import _KEYEVENTF_KEYUP

    inp._write_clipboard("hello world from azmuth")
    await asyncio.sleep(0.1)

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
    print("\n=== Step 7: verify the text landed ===")
    windows = await auto.list_windows()
    notes = [w for w in windows if "notepad" in (w.title or "").lower()]
    found = False
    for w in notes:
        print(f"  window title: {w.title!r}")
        if "hello world from azmuth" in w.title:
            print("  ✓ verified: content landed in Notepad")
            found = True
            break
    if not found:
        print("  ✗ expected text not in any Notepad title")


if __name__ == "__main__":
    asyncio.run(main())