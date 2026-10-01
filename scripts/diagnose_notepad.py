import asyncio
import os
from pathlib import Path

from app.automation import ApplicationPolicy, PathPolicy, WindowsAutomationImpl


async def main() -> None:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    notepad = Path(windir) / "System32" / "notepad.exe"

    impl = WindowsAutomationImpl(
        application_policy=ApplicationPolicy(aliases={"notepad": str(notepad)}),
        path_policy=PathPolicy(),
    )

    result = await impl.open_application("notepad")
    print(f"launched: alias={result.alias} pid={result.pid}")

    await asyncio.sleep(1.0)  # let the redirector exit and the app settle

    procs = await impl.list_processes()
    notepads = [p for p in procs if p.name.lower() == "notepad.exe"]
    print("notepad.exe processes now running:")
    for p in notepads:
        print(f"  pid={p.pid} name={p.name} mem={p.memory_kb} KB")

    print(f"launcher pid {result.pid} still alive? "
          f"{any(p.pid == result.pid for p in notepads)}")


if __name__ == "__main__":
    asyncio.run(main())