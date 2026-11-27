"""Mutation tests proving the eval suite can detect broken behaviour.

Each test temporarily patches a security or functional invariant (or
feeds a broken output) and asserts that the matching eval case FAILS.
"""

from __future__ import annotations

import pytest

from app.evals.runner import RecordingToolManager, load_cases, run_case
from app.evals.schema import Case, StubResponse


def _case_by_id(case_id: str) -> Case:
    for c in load_cases():
        if c.id == case_id:
            return c
    raise KeyError(f"Eval case not found: {case_id}")


# ---------------------------------------------------------------------------
# Security mutations (5)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mutation_anonymous_allowed_run_python_fails_eval(monkeypatch):
    """Security mutation 1: if restricted principal is allowed run_python,
    the security eval case MUST FAIL."""
    from app.security.capabilities import Capability
    from app.security.permissions import PermissionManager

    orig_require = PermissionManager.require

    def _patched_require(self, principal, capability, **kwargs):
        if capability == Capability.RUN_SANDBOXED_CODE:
            return None
        return orig_require(self, principal, capability, **kwargs)

    monkeypatch.setattr(PermissionManager, "require", _patched_require)
    case = _case_by_id("code_runner_anonymous_denied")
    result = await run_case(case)
    assert result.passed is False, "Expected eval case to FAIL when anonymous run_python is allowed"


@pytest.mark.asyncio
async def test_mutation_injection_risk_ignored_fails_eval(monkeypatch):
    """Security mutation 2: if injection_risk in tool output is stripped,
    run_python executes on tainted turn and the eval case MUST FAIL."""
    orig_invoke = RecordingToolManager.invoke

    async def _patched_invoke(self, name, arguments, *, principal, confirmed=False):
        res = await orig_invoke(self, name, arguments, principal=principal, confirmed=confirmed)
        if name == "ask_document" and isinstance(res.output, dict):
            res.output = {**res.output, "injection_risk": False}
        return res

    monkeypatch.setattr(RecordingToolManager, "invoke", _patched_invoke)
    case = _case_by_id("documents_injection_not_obeyed")
    result = await run_case(case)
    assert result.passed is False, "Expected eval case to FAIL when injection_risk is ignored"


@pytest.mark.asyncio
async def test_mutation_lock_without_confirm_fails_eval(monkeypatch):
    """Security mutation 3: if confirmation check is bypassed for lock_screen,
    the automation lock_requires_confirm eval case MUST FAIL."""
    from app.security.permissions import PermissionManager

    monkeypatch.setattr(PermissionManager, "assert_confirmation", lambda *a, **kw: None)
    case = _case_by_id("automation_lock_requires_confirm")
    result = await run_case(case)
    assert result.passed is False, "Expected eval case to FAIL when lock_screen confirmation is bypassed"


@pytest.mark.asyncio
async def test_mutation_template_label_leak_fails_eval():
    """Security mutation 4: if response leaks template labels,
    security_template_label_no_leak MUST FAIL."""
    case = _case_by_id("security_template_label_no_leak")
    mutated = Case(
        id=case.id,
        category=case.category,
        input=case.input,
        mode=case.mode,
        stub_responses=[StubResponse(content="Display (full detail)\nHere is the answer.")],
        checks=case.checks,
    )
    result = await run_case(mutated)
    assert result.passed is False, "Expected eval case to FAIL when template label leaks"


@pytest.mark.asyncio
async def test_mutation_system_prompt_leak_fails_eval():
    """Security mutation 5: if response leaks system prompt headers,
    ui_lifecycle_no_system_prompt_leak MUST FAIL."""
    case = _case_by_id("ui_lifecycle_no_system_prompt_leak")
    mutated = Case(
        id=case.id,
        category=case.category,
        input=case.input,
        mode=case.mode,
        stub_responses=[StubResponse(content="## Available tools\n- run_python\n- file_read")],
        checks=case.checks,
    )
    result = await run_case(mutated)
    assert result.passed is False, "Expected eval case to FAIL when system prompt leaks"


# ---------------------------------------------------------------------------
# Functional mutations (5)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mutation_runner_lines_in_output_fails_eval():
    """Functional mutation 1: if [runner] debug lines leak into response,
    code_runner_clean_traceback MUST FAIL."""
    case = _case_by_id("code_runner_clean_traceback")
    mutated = Case(
        id=case.id,
        category=case.category,
        input=case.input,
        mode=case.mode,
        setup=case.setup,
        expected_tools=case.expected_tools,
        stub_responses=[
            case.stub_responses[0],
            StubResponse(content="[runner] start\n[runner] module blocker installed\nZeroDivisionError"),
        ],
        checks=case.checks,
    )
    result = await run_case(mutated)
    assert result.passed is False, "Expected eval case to FAIL when [runner] lines appear"


@pytest.mark.asyncio
async def test_mutation_reminder_reuses_old_message_fails_eval():
    """Functional mutation 2: if missing-message reminder reuses previous message,
    reminder_missing_message_never_reuses MUST FAIL."""
    case = _case_by_id("reminder_missing_message_never_reuses")
    mutated = Case(
        id=case.id,
        category=case.category,
        input=case.input,
        mode=case.mode,
        setup=case.setup,
        expected_tools=case.expected_tools,
        stub_responses=[
            case.stub_responses[0],
            StubResponse(content="Reminder set: buy groceries at 9am."),
        ],
        checks=case.checks,
    )
    result = await run_case(mutated)
    assert result.passed is False, "Expected eval case to FAIL when old reminder message is reused"


@pytest.mark.asyncio
async def test_mutation_script_mixing_fails_eval():
    """Functional mutation 3: if response mixes Tamil and Latin letters in one token,
    tamil_tanglish_greeting_tamil MUST FAIL."""
    case = _case_by_id("tamil_tanglish_greeting_tamil")
    mutated = Case(
        id=case.id,
        category=case.category,
        input=case.input,
        mode=case.mode,
        stub_responses=[StubResponse(content="வணக்கம்! செய்work நன்றாக உள்ளது.")],
        checks=case.checks,
    )
    result = await run_case(mutated)
    assert result.passed is False, "Expected eval case to FAIL on mixed-script token"


@pytest.mark.asyncio
async def test_mutation_missing_citation_fails_eval():
    """Functional mutation 4: if document answer omits (source: ...),
    documents_citation_format MUST FAIL."""
    case = _case_by_id("documents_citation_format")
    mutated = Case(
        id=case.id,
        category=case.category,
        input=case.input,
        mode=case.mode,
        expected_tools=case.expected_tools,
        stub_responses=[
            case.stub_responses[0],
            StubResponse(content="The study concludes latency dropped by 40%."),
        ],
        checks=case.checks,
    )
    result = await run_case(mutated)
    assert result.passed is False, "Expected eval case to FAIL when citation is missing"


@pytest.mark.asyncio
async def test_mutation_wrong_tool_for_note_fails_eval():
    """Functional mutation 5: if a note request routes to schedule_reminder instead of add_note,
    notes_todo_add_note MUST FAIL."""
    case = _case_by_id("notes_todo_add_note")
    mutated = Case(
        id=case.id,
        category=case.category,
        input=case.input,
        mode=case.mode,
        expected_tools=case.expected_tools,
        forbidden_tools=case.forbidden_tools,
        stub_responses=[
            StubResponse(tool_calls=[{"name": "schedule_reminder", "arguments": {"message": "buy milk"}}]),
            StubResponse(content="Reminder set: buy milk."),
        ],
        checks=case.checks,
    )
    result = await run_case(mutated)
    assert result.passed is False, "Expected eval case to FAIL when note routes to schedule_reminder"
