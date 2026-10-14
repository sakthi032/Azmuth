"""Standalone bridge runner.

Starts the browser bridge and stays running, printing connection status
every 3 seconds. Use this to watch the extension connect in real time.

Run:  py -m scripts.run_bridge
Stop: Ctrl+C
"""

from __future__ import annotations

import time

from app.automation.chrome_extension_bridge import get_bridge


def main() -> None:
    bridge = get_bridge()
    time.sleep(1.0)

    if bridge.bind_error:
        print(f"BIND FAILED: {bridge.bind_error}")
        print("Port 9280 is already in use by another process.")
        print("Kill it with: netstat -ano | findstr :9280  (look at the last column)")
        return

    print("bridge listening on 127.0.0.1:9280")
    print("loading the extension in a Chromium browser will connect it")
    print("Ctrl+C to stop")
    print()

    last_state: tuple[str, ...] = ()
    try:
        while True:
            time.sleep(3)
            now = bridge.connected_browsers()
            if now != last_state:
                if now:
                    print(f"[+] connected: {list(now)}")
                else:
                    print("[-] no browsers connected")
                last_state = now

            if now:
                try:
                    r = bridge.send_command(now[0], "list", {})
                    tabs = r.get("tabs", [])
                    print(f"    {now[0]}: {len(tabs)} tabs visible")
                except Exception as exc:
                    print(f"    list failed: {exc}")
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()