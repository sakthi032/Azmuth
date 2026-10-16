"""End-to-end test of the three-tier resolver."""
from __future__ import annotations
import asyncio
import inspect


async def main() -> None:
    from app.automation.dpi import query_spaces
    from app.automation.input import InputController
    from app.automation.resolver import TargetResolver
    from app.automation.uia import UIAutomationController
    from app.automation.windows import WindowsAutomationImpl

    # -- Introspection first --
    print("=== environment check ===")
    sig = inspect.signature(UIAutomationController.find_elements)
    print(f"  find_elements signature: {sig}")
    print(f"  has 'window' param: {'window' in sig.parameters}")
    print(f"  dpi spaces: {query_spaces().as_dict()}")
    print()

    uia = UIAutomationController()
    inp = InputController()
    auto = WindowsAutomationImpl()
    resolver = TargetResolver(uia=uia, input_ctl=inp)

    print("=== launch Notepad ===")
    await auto.open_application("notepad")
    await asyncio.sleep(2.5)

    windows = await auto.list_windows()
    notes = [w for w in windows if "notepad" in (w.title or "").lower()]
    if not notes:
        print("FAIL: no Notepad window"); return
    target = notes[0]
    await auto.focus_window(str(target.handle))
    await asyncio.sleep(0.5)
    print(f"  target: hwnd={target.handle} title={target.title!r}")
    print()

    # Direct UIA probe — did it find "File" at all?
    print("=== direct UIA probe for 'File' ===")
    elems = await uia.find_elements(name="File", limit=5, window=target.title)
    print(f"  UIA found {len(elems)} matches")
    for e in elems[:3]:
        print(f"    {e.as_dict()}")
    print()

    # Test 1: UIA hit
    print("=== resolve 'File' (expect UIA) ===")
    r = await resolver.resolve("File", window=target.title)
    print(f"  {r.as_dict()}")

    # Test 3: pure OCR — text that only exists as pixels
    print()
    print("=== resolve 'Untitled' (expect CV) ===")
    r = await resolver.resolve("Untitled", window=target.title)
    print(f"  {r.as_dict()}")


if __name__ == "__main__":
    asyncio.run(main())