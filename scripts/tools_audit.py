
"""Show every registered tool and why it is or is not visible.

Run:  py -m scripts.tools_audit
"""

from __future__ import annotations

from app.brain import build_azmuth
from app.config.settings import load_settings
from app.security.modes import AssuranceLevel, PermissionMode, Principal


def main() -> None:
    azmuth = build_azmuth(load_settings())
    registry = azmuth.tools.registry

    print("=" * 78)
    print("REGISTERED TOOLS")
    print("=" * 78)
    for tool in registry.tools():
        try:
            avail = tool.is_available()
        except Exception as exc:  # noqa: BLE001
            avail = f"ERROR: {exc}"
        print(f"  {tool.name:25}  cap={tool.capability.value:22}  avail={avail}")

    print()
    print("=" * 78)
    print("VISIBLE TO CREATOR")
    print("=" * 78)
    creator = Principal(
        user_id="creator",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
    )
    names = sorted(m.name for m in azmuth.tools.list_metadata(principal=creator))
    for name in names:
        print(f"  {name}")

    print()
    print("=" * 78)
    print("VISIBLE TO RESTRICTED")
    print("=" * 78)
    anon = Principal.anonymous()
    names = sorted(m.name for m in azmuth.tools.list_metadata(principal=anon))
    for name in names:
        print(f"  {name}")


if __name__ == "__main__":
    main()

# py -m scripts.tools_audit