
"""Show whether chrome_close_tab is available to the AI right now.

Run:  py -m scripts.tab_tool_check
"""

from __future__ import annotations

from app.automation.chrome import ChromeDevTools
from app.brain import build_azmuth
from app.config.settings import load_settings
from app.security.modes import AssuranceLevel, PermissionMode, Principal


def main() -> None:
    print("=" * 70)
    print("1. Is Chrome's debug port reachable?")
    print("=" * 70)
    chrome = ChromeDevTools()
    print(f"   chrome_devtools.is_available(): {chrome.is_available()}")
    if chrome.is_available():
        tabs = chrome.list_tabs()
        print(f"   tabs visible: {len(tabs)}")
        for t in tabs[:10]:
            print(f"     - {t.title[:70]}")
    else:
        print("   -> Chrome is NOT running with --remote-debugging-port=9222")
        print("   -> The chrome_close_tab tool will be hidden from the AI")
    print()

    print("=" * 70)
    print("2. Does the AI see chrome_close_tab?")
    print("=" * 70)
    azmuth = build_azmuth(load_settings())
    creator = Principal(
        user_id="creator",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
    )
    visible = sorted(m.name for m in azmuth.tools.list_metadata(principal=creator))
    tab_tools = [n for n in visible if "chrome" in n]
    print(f"   chrome tools visible to CREATOR: {tab_tools}")
    if not tab_tools:
        print("   -> chrome_close_tab is NOT in the AI's list")
        print("   -> root cause: chrome debug port not reachable (see section 1)")
    print()

    print("=" * 70)
    print("3. What the AI would see on a tab request")
    print("=" * 70)
    print(f"   total tools: {len(visible)}")
    print(f"   has chrome_close_tab: {'chrome_close_tab' in visible}")
    print(f"   has close_application: {'close_application' in visible}")
    azmuth.memory.close()


if __name__ == "__main__":
    main()

# py -m scripts.tab_tool_check