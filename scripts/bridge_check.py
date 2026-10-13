"""Diagnose the multi-browser extension bridge."""

from __future__ import annotations

import time


def main() -> None:
    print("=== 1. websockets package ===")
    try:
        import websockets
        print(f"   OK - websockets {websockets.__version__}")
    except ImportError:
        print("   MISSING. Run: pip install websockets")
        return
    print()

    print("=== 2. Starting bridge ===")
    from app.automation.chrome_extension_bridge import get_bridge
    bridge = get_bridge()
    time.sleep(1.5)
    if bridge.bind_error:
        print(f"   BIND FAILED: {bridge.bind_error}")
        return
    print("   bridge listening on 127.0.0.1:9280")
    print()

    print("=== 3. Waiting up to 10s for browsers to connect ===")
    for i in range(20):
        time.sleep(0.5)
        browsers = bridge.connected_browsers()
        if browsers:
            print(f"   connected after {i * 0.5:.1f}s: {list(browsers)}")
            print()
            print("=== 4. Test command per browser ===")
            for b in browsers:
                try:
                    result = bridge.send_command(b, "list", {})
                    tabs = result.get("tabs", [])
                    print(f"   {b}: {len(tabs)} tabs")
                    for t in tabs[:3]:
                        pinned = " [PINNED]" if t.get("pinned") else ""
                        print(f"     #{t.get('id')} {t.get('title','')[:55]}{pinned}")
                except Exception as exc:
                    print(f"   {b}: command failed: {exc}")
            return

    print("   NO BROWSERS CONNECTED after 10s")
    print()
    print("To connect a browser:")
    print("  1. Open it (chrome, edge, brave, opera, vivaldi)")
    print("  2. Go to its extensions page in dev mode")
    print("  3. Load unpacked -> data/chrome_extension")


if __name__ == "__main__":
    main()