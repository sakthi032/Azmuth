"""Console, JSON, and HTML reports for eval runs."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.evals.schema import RunResult


RESULTS_DIR = Path(__file__).parent.parent.parent / "results"


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------
def render_console(result: RunResult) -> str:
    lines = []
    lines.append("")
    lines.append(f"  Eval run — {result.category}")
    lines.append(f"  Started:  {result.started_at}")
    lines.append(f"  Finished: {result.finished_at}")
    lines.append(f"  Duration: {result.total_duration_ms:.0f} ms")
    lines.append("")

    # Per-category breakdown
    by_cat: dict[str, list] = defaultdict(list)
    for cr in result.cases:
        by_cat[cr.case.category].append(cr)

    lines.append(f"  {'Category':<20s} {'Pass':>5s} {'Total':>6s} {'Rate':>6s}")
    lines.append(f"  {'-'*20} {'-'*5} {'-'*6} {'-'*6}")
    for cat in sorted(by_cat):
        items = by_cat[cat]
        passed = sum(1 for c in items if c.passed)
        rate = 100 * passed / len(items) if items else 0
        lines.append(
            f"  {cat:<20s} {passed:>5d} {len(items):>6d} {rate:>5.1f}%"
        )
    lines.append(f"  {'-'*20} {'-'*5} {'-'*6} {'-'*6}")
    lines.append(
        f"  {'TOTAL':<20s} {result.passed:>5d} {result.total:>6d} "
        f"{100*result.pass_rate:>5.1f}%"
    )
    lines.append("")

    # Failures
    failures = [c for c in result.cases if not c.passed]
    if failures:
        lines.append("  Failing cases:")
        for cr in failures:
            lines.append(f"    - {cr.case.id}")
            if cr.error:
                lines.append(f"        error: {cr.error}")
            for jr in cr.judge_results:
                if not jr.passed:
                    lines.append(f"        × {jr.reason}")
            lines.append(
                f"        input:   {cr.case.input[:80]!r}"
            )
            lines.append(
                f"        tools:   {cr.actual_tools}"
            )
            lines.append(
                f"        response: {cr.response_text[:120]!r}"
            )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def write_json(result: RunResult, *, save_baseline: bool = False) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"eval_{ts}.json"
    payload = _to_dict(result)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if save_baseline:
        baseline = RESULTS_DIR / "baseline.json"
        baseline.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return path


def _to_dict(result: RunResult) -> dict:
    return {
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_ms": result.total_duration_ms,
        "category": result.category,
        "summary": {
            "total": result.total,
            "passed": result.passed,
            "failed": result.failed,
            "pass_rate": round(result.pass_rate, 4),
        },
        "cases": [
            {
                "id": cr.case.id,
                "category": cr.case.category,
                "passed": cr.passed,
                "input": cr.case.input,
                "expected_tools": cr.case.expected_tools,
                "forbidden_tools": cr.case.forbidden_tools,
                "actual_tools": cr.actual_tools,
                "response_text": cr.response_text[:500],
                "model": cr.model,
                "provider": cr.provider,
                "duration_ms": cr.duration_ms,
                "error": cr.error,
                "judges": [
                    {"check": j.check_type, "passed": j.passed,
                     "reason": j.reason}
                    for j in cr.judge_results
                ],
            }
            for cr in result.cases
        ],
    }


# ---------------------------------------------------------------------------
# Baseline diff
# ---------------------------------------------------------------------------
def diff_against_baseline(result: RunResult) -> dict:
    baseline_path = RESULTS_DIR / "baseline.json"
    if not baseline_path.is_file():
        return {"baseline": None, "regressions": [], "improvements": []}
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except Exception:
        return {"baseline": None, "regressions": [], "improvements": []}

    old = {c["id"]: c["passed"] for c in baseline.get("cases", [])}
    regressions, improvements = [], []
    for cr in result.cases:
        was = old.get(cr.case.id)
        if was is None:
            continue
        if was and not cr.passed:
            regressions.append(cr.case.id)
        elif not was and cr.passed:
            improvements.append(cr.case.id)
    baseline_summary = baseline.get("summary") or {}
    baseline_rate = baseline_summary.get("pass_rate")
    return {
        "baseline": str(baseline_path),
        "baseline_pass_rate": baseline_rate,
        "regressions": regressions,
        "improvements": improvements,
    }


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def write_html(result: RunResult) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"eval_{ts}.html"
    path.write_text(_render_html(result), encoding="utf-8")
    return path


def _render_html(result: RunResult) -> str:
    by_cat: dict[str, list] = defaultdict(list)
    for cr in result.cases:
        by_cat[cr.case.category].append(cr)

    cat_rows = ""
    for cat in sorted(by_cat):
        items = by_cat[cat]
        passed = sum(1 for c in items if c.passed)
        rate = 100 * passed / len(items) if items else 0
        color = "#a6e3a1" if rate >= 80 else "#f9e2af" if rate >= 50 else "#f38ba8"
        cat_rows += (
            f"<tr><td>{html.escape(cat)}</td>"
            f"<td>{passed}</td><td>{len(items)}</td>"
            f"<td style='color:{color}'>{rate:.1f}%</td></tr>"
        )

    case_rows = ""
    for cr in result.cases:
        if cr.passed:
            continue
        judges_html = "<br>".join(
            f"<span style='color:#f38ba8'>✗ {html.escape(j.reason)}</span>"
            for j in cr.judge_results if not j.passed
        )
        case_rows += (
            f"<tr><td>{html.escape(cr.case.id)}</td>"
            f"<td>{html.escape(cr.case.input[:120])}</td>"
            f"<td>{html.escape(str(cr.actual_tools))}</td>"
            f"<td>{html.escape(cr.response_text[:200])}</td>"
            f"<td>{judges_html or html.escape(cr.error)}</td>"
            f"<td>{cr.duration_ms:.0f} ms</td></tr>"
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Eval {result.started_at}</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: #1e1e2e;
       color: #cdd6f4; padding: 24px; }}
h1, h2 {{ color: #89b4fa; }}
table {{ border-collapse: collapse; margin: 12px 0 24px; }}
th, td {{ padding: 6px 12px; border-bottom: 1px solid #313244;
         text-align: left; vertical-align: top; }}
th {{ color: #a6adc8; font-weight: 600; }}
.summary {{ color: #a6adc8; margin-bottom: 24px; }}
</style></head><body>
<h1>Azmuth Eval — {html.escape(result.category)}</h1>
<p class="summary">
Started: {html.escape(result.started_at)}<br>
Duration: {result.total_duration_ms:.0f} ms<br>
Total: {result.total} &nbsp; Passed: {result.passed} &nbsp;
Failed: {result.failed} &nbsp; Rate: {100*result.pass_rate:.1f}%
</p>
<h2>Per category</h2>
<table><tr><th>Category</th><th>Pass</th><th>Total</th><th>Rate</th></tr>
{cat_rows}
</table>
<h2>Failures</h2>
{"<table><tr><th>ID</th><th>Input</th><th>Actual tools</th><th>Response</th><th>Reason</th><th>Latency</th></tr>" + case_rows + "</table>" if case_rows else "<p>All passed.</p>"}
</body></html>"""


__all__ = [
    "render_console", "write_json", "write_html", "diff_against_baseline",
]