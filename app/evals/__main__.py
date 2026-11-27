"""CLI: python -m app.evals"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.evals.report import (
    diff_against_baseline, render_console, write_html, write_json,
)
from app.evals.runner import run_all, run_category


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.evals")
    p.add_argument("--category", default=None,
                   help="run only one category (file stem)")
    p.add_argument("--fast", action="store_true",
                   help="run at least 2 cases per category")
    p.add_argument("--live", action="store_true",
                   help="(round 2) use real providers")
    p.add_argument("--threshold", type=float, default=0.80,
                   help="exit 1 if pass rate < threshold (default 0.80)")
    p.add_argument("--save-baseline", action="store_true",
                   help="also write results/baseline.json")
    p.add_argument("--no-html", action="store_true",
                   help="skip HTML report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.live:
        print("--live is not implemented yet (round 2).", file=sys.stderr)
        return 2

    async def _run():
        if args.category:
            return await run_category(args.category)
        return await run_all(fast=args.fast)

    result = asyncio.run(_run())

    print(render_console(result))

    json_path = write_json(result, save_baseline=args.save_baseline)
    print(f"  JSON:   {json_path}")

    if not args.no_html:
        html_path = write_html(result)
        print(f"  HTML:   {html_path}")

    diff = diff_against_baseline(result)
    if diff["baseline"]:
        print(f"  Compared against baseline: {diff['baseline']}")
        if diff["regressions"]:
            print(f"  Regressions:  {diff['regressions']}")
        if diff["improvements"]:
            print(f"  Improvements: {diff['improvements']}")
    else:
        print("  No baseline found (use --save-baseline to create one).")

    if result.total == 0:
        print("  No cases found.", file=sys.stderr)
        return 2

    if result.pass_rate < args.threshold:
        print(
            f"  FAIL: pass rate {100*result.pass_rate:.1f}% < "
            f"threshold {100*args.threshold:.0f}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())