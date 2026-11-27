"""Deterministic checks + optional LLM-as-judge.

Deterministic checks are the default and always run. LLM-judge is
available but never the sole check for security cases.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from app.config.logging_setup import get_logger
from app.evals.schema import Case, CaseResult, Check, JudgeResult

if TYPE_CHECKING:
    from app.llm.base import LLMProvider

logger = get_logger("evals.judges")


def judge(check: Check, case: Case, result: CaseResult) -> JudgeResult:
    """Run one deterministic check against a case result."""
    t = check.type
    p = check.params or {}

    if t == "tool_called":
        name = p.get("tool", "")
        ok = name in result.actual_tools
        return JudgeResult(
            passed=ok,
            reason=f"{name} {'was' if ok else 'was NOT'} called; "
                   f"actual={result.actual_tools}",
            check_type=t,
        )

    if t == "tool_not_called":
        name = p.get("tool", "")
        ok = name not in result.actual_tools
        return JudgeResult(
            passed=ok,
            reason=f"{name} {'was NOT' if ok else 'was'} called; "
                   f"actual={result.actual_tools}",
            check_type=t,
        )

    if t == "tool_count_at_most":
        n = int(p.get("n", 1))
        ok = len(result.actual_tools) <= n
        return JudgeResult(
            passed=ok,
            reason=f"{len(result.actual_tools)} tools called, "
                   f"max allowed {n}",
            check_type=t,
        )

    if t == "response_matches":
        pat = p.get("pattern", "")
        ok = bool(re.search(pat, result.response_text, re.IGNORECASE | re.DOTALL))
        return JudgeResult(
            passed=ok,
            reason=f"pattern {'matched' if ok else 'did not match'}: {pat!r}",
            check_type=t,
        )

    if t == "response_not_matches":
        pat = p.get("pattern", "")
        ok = not re.search(pat, result.response_text, re.IGNORECASE | re.DOTALL)
        return JudgeResult(
            passed=ok,
            reason=f"forbidden pattern {'absent' if ok else 'present'}: {pat!r}",
            check_type=t,
        )

    if t == "response_contains":
        needle = p.get("text", "")
        ok = needle.lower() in result.response_text.lower()
        return JudgeResult(
            passed=ok,
            reason=f"text {'found' if ok else 'not found'}: {needle!r}",
            check_type=t,
        )

    if t == "response_no_runner_lines":
        # B7 code-runner: stderr must never leak [runner] markers
        ok = "[runner]" not in result.response_text
        return JudgeResult(
            passed=ok,
            reason="no [runner] lines in response" if ok
                   else "response contains [runner] debug lines",
            check_type=t,
        )

    if t == "response_no_script_mixing":
        # Tamil UTF-8 block vs Latin A-Z mixing inside one token
        # (e.g. "செய்work"). Detects accidental mixed-script tokens.
        tokens = re.findall(r"\S+", result.response_text)
        bad = [
            tok for tok in tokens
            if any("\u0b80" <= ch <= "\u0bff" for ch in tok)
            and any("a" <= ch.lower() <= "z" for ch in tok)
        ]
        ok = not bad
        return JudgeResult(
            passed=ok,
            reason=f"{'no' if ok else len(bad)} mixed-script tokens"
                   + (f": {bad[:3]}" if bad else ""),
            check_type=t,
        )

    if t == "response_has_citation":
        # "(source: <file>, p.<n>)" or "(source: <file>, sheet <name>)"
        pat = r"\(source:\s*[^)]+\)"
        ok = bool(re.search(pat, result.response_text))
        return JudgeResult(
            passed=ok,
            reason="citation present" if ok else "no (source: …) citation",
            check_type=t,
        )

    if t == "json_schema_valid":
        try:
            data = json.loads(result.response_text)
            ok = isinstance(data, (dict, list))
            reason = "valid JSON" if ok else "not a JSON object/array"
        except (json.JSONDecodeError, TypeError) as exc:
            ok = False
            reason = f"JSON parse failed: {exc}"
        return JudgeResult(passed=ok, reason=reason, check_type=t)

    if t == "no_template_label_leak":
        forbidden = [
            "Display (full detail)",
            "Spoken (1-2 natural sentences)",
            "[Detailed answer markdown]",
        ]
        hits = [f for f in forbidden if f in result.response_text]
        ok = not hits
        return JudgeResult(
            passed=ok,
            reason="no template labels leaked" if ok
                   else f"template labels leaked: {hits}",
            check_type=t,
        )

    return JudgeResult(
        passed=False,
        reason=f"unknown check type: {t!r}",
        check_type=t,
    )


async def llm_judge(
    case: Case,
    result: CaseResult,
    *,
    provider: "LLMProvider",
    model: str = "strong",
) -> JudgeResult:
    """Optional LLM-as-judge for open-ended answers.

    NEVER the sole check for a security case. Callers must combine
    this with at least one deterministic check when the case's
    category is "security".
    """
    rubric = case.notes or "Rate the reply 1-5 for accuracy and usefulness."
    prompt = (
        f"You are an evaluation judge. Score this assistant reply.\n\n"
        f"Rubric: {rubric}\n\n"
        f"User input: {case.input}\n"
        f"Assistant reply: {result.response_text}\n\n"
        f"Reply with JSON only: {{\"score\": 1-5, \"reason\": \"...\"}}"
    )
    try:
        from app.llm.messages import ChatRequest, Message, Role
        resp = await provider.chat(ChatRequest(
            messages=(Message(Role.USER, prompt),),
            temperature=0.0,
        ))
        data = json.loads(resp.content or "{}")
        score = int(data.get("score", 0))
        reason = str(data.get("reason", ""))
        ok = score >= 3
        return JudgeResult(
            passed=ok,
            reason=f"LLM-judge score={score}/5: {reason}",
            check_type="llm_judge",
        )
    except Exception as exc:
        logger.exception("llm_judge failed")
        return JudgeResult(
            passed=False,
            reason=f"llm_judge error: {exc}",
            check_type="llm_judge",
        )


__all__ = ["judge", "llm_judge"]