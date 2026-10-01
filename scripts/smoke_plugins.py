"""Manual smoke test for the plugin subsystem.

Run:  py -m scripts.smoke_plugins
"""

import asyncio
from pathlib import Path

from app.plugins import build_plugin_manager
from app.security import PermissionManager, Principal
from app.tools import ToolManager


async def main() -> None:
    manager, report = build_plugin_manager()

    print("LOADED PLUGINS")
    for md in manager.metadata():
        caps = ", ".join(c.value for c in md.required_capabilities) or "(none)"
        print(f"  {md.name:12}  v{md.version}  caps=[{caps}]")
        print(f"               {md.description}")

    if report.failure_count:
        print()
        print("FAILURES")
        for failure in report.failures:
            print(f"  {failure.directory}")
            print(f"    {failure.reason}")

    # Register plugin tools and invoke one.
    tools = ToolManager(PermissionManager())
    manager.register_tools(tools)

    print()
    print("TOOLS CONTRIBUTED BY PLUGINS")
    print(f"  {list(tools.names())}")

    principal = Principal.anonymous()
    if "uppercase" in tools.names():
        result = await tools.invoke(
            "uppercase", {"text": "hello from azmuth"}, principal=principal
        )
        print()
        print(f"  uppercase('hello from azmuth') = {result.output!r}")


if __name__ == "__main__":
    asyncio.run(main())