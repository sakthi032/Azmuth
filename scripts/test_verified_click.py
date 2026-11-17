"""Test the full verified-click + wait chain against Notepad."""
from __future__ import annotations
import asyncio


async def main() -> None:
    from app.automation.input import InputController
    from app.automation.resolver import TargetResolver
    from app.automation.uia import UIAutomationController
    from app.automation.verifier import Verifier
    from app.automation.windows import WindowsAutomationImpl
    from app.tools.builtin.wait_tools import WaitForElementTool

    uia = UIAutomationController()
    inp = InputController()
    auto = WindowsAutomationImpl()
    resolver = TargetResolver(uia=uia, input_ctl=inp)
    verifier = Verifier(uia)
    waiter = WaitForElementTool(uia)

    await auto.open_application("notepad")
    await asyncio.sleep(2.5)

    windows = await auto.list_windows()
    notes = [w for w in windows if "notepad" in (w.title or "").lower()]
    if not notes:
        print("FAIL: no Notepad window"); return
    target = notes[0]
    await auto.focus_window(str(target.handle))
    await asyncio.sleep(0.5)

    print("=== snapshot before ===")
    before = await verifier.snapshot(target.title)
    print(f"  element count: {before.element_count}")

    print("\n=== click 'File' menu via resolver ===")
    r = await resolver.resolve("File", window=target.title)
    print(f"  tier={r.tier.value}  click={r.click_x},{r.click_y}")
    await inp.click(r.click_x, r.click_y)
    await asyncio.sleep(0.5)

    print("\n=== snapshot after ===")
    after = await verifier.snapshot(target.title)
    print(f"  element count: {after.element_count}")
    print(f"  changed: {Verifier.changed(before, after)}")
    if Verifier.changed(before, after):
        print(f"  what: {Verifier.describe_change(before, after)}")

    print("\n=== wait for menu item 'New' to appear ===")
    result = await waiter.execute(
        name="New",
        control_type="MenuItem",
        window=target.title,
        timeout=3.0,
    )
    print(f"  {result}")

    print("\n=== press Escape to close the menu ===")
    await inp.press_keys("esc")
    await asyncio.sleep(0.3)

    print("\n=== wait for menu item 'New' to be gone ===")
    from app.tools.builtin.wait_tools import WaitForAbsenceTool
    absence = WaitForAbsenceTool(uia)
    result = await absence.execute(
        name="New",
        control_type="MenuItem",
        window=target.title,
        timeout=3.0,
    )
    print(f"  {result}")


if __name__ == "__main__":
    asyncio.run(main())