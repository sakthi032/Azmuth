"""Standalone sandbox diagnostic. Run from project root:
    py scripts/test_sandbox.py

Prints the raw stdout/stderr so we can see the actual runner failure.
"""
from __future__ import annotations

import asyncio
import sys

from app.tools.builtin.code_exec import CodeSandbox


async def main() -> int:
    sandbox = CodeSandbox()

    cases = [
        ("simple", "print(2 + 2)", 15.0),
        ("empty", "pass", 10.0),
        ("import", "import sys; print(sys.version)", 10.0),
    ]

    for name, code, timeout in cases:
        print(f"\n=== {name} ===")
        print(f"code: {code!r}")
        try:
            result = await sandbox.execute(code, timeout=timeout)
        except Exception as exc:
            print(f"EXCEPTION from sandbox.execute: {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
            continue

        print(f"success:    {result.get('success')}")
        print(f"exit_code:  {result.get('exit_code')}")
        print(f"timed_out:  {result.get('timed_out')}")
        print(f"duration:   {result.get('duration_ms')} ms")
        print(f"--- stdout ({len(result.get('stdout') or '')} bytes) ---")
        print(result.get("stdout") or "(empty)")
        print(f"--- stderr ({len(result.get('stderr') or '')} bytes) ---")
        print(result.get("stderr") or "(empty)")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))