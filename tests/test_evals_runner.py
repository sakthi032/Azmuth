"""Unit tests for the eval runner."""

from __future__ import annotations

import pytest

from app.evals.runner import (
    ScriptedLLM, load_cases, run_case, run_category,
)
from app.evals.schema import Case, StubResponse


def test_load_cases_returns_all():
    cases = load_cases()
    assert len(cases) >= 90, f"expected >=90 cases, got {len(cases)}"
    ids = {c.id for c in cases}
    assert len(ids) == len(cases), "duplicate case ids"


def test_load_cases_one_category():
    chat = load_cases(category="chat")
    assert all(c.category == "chat" for c in chat)
    assert len(chat) >= 15


def test_scripted_llm_returns_in_order():
    from app.llm.messages import ChatRequest, Message, Role
    llm = ScriptedLLM([
        StubResponse(content="one"),
        StubResponse(content="two"),
    ])
    import asyncio
    async def _go():
        r1 = await llm.chat(ChatRequest(messages=(Message(Role.USER, "hi"),)))
        r2 = await llm.chat(ChatRequest(messages=(Message(Role.USER, "hi"),)))
        return r1, r2
    r1, r2 = asyncio.run(_go())
    assert r1.content == "one"
    assert r2.content == "two"


@pytest.mark.asyncio
async def test_simple_case_passes():
    case = Case(
        id="t_simple",
        category="chat",
        input="hi",
        stub_responses=[StubResponse(content="Hello!")],
        checks=[],
    )
    result = await run_case(case)
    assert result.passed, (
        f"expected pass, got judges: "
        f"{[(j.check_type, j.passed, j.reason) for j in result.judge_results]}"
    )
    assert result.response_text == "Hello!"


@pytest.mark.asyncio
async def test_tool_called_check():
    case = Case(
        id="t_tool",
        category="tool_selection",
        input="weather?",
        stub_responses=[
            StubResponse(tool_calls=[{"name": "get_weather"}]),
            StubResponse(content="sunny"),
        ],
        expected_tools=["get_weather"],
    )
    result = await run_case(case)
    assert "get_weather" in result.actual_tools
    assert result.passed


@pytest.mark.asyncio
async def test_forbidden_tool_fails_case():
    case = Case(
        id="t_forbidden",
        category="tool_selection",
        input="?",
        stub_responses=[
            StubResponse(tool_calls=[{"name": "search_web"}]),
            StubResponse(content="ok"),
        ],
        forbidden_tools=["search_web"],
    )
    result = await run_case(case)
    assert not result.passed


@pytest.mark.asyncio
async def test_run_category_aggregates():
    result = await run_category("chat")
    assert result.total >= 8
    assert 0.0 <= result.pass_rate <= 1.0
    

def test_cases_dir_contains_expected_files():
    """Guard against silently missing category files (root cause of
    '--category chat' returning 0 cases)."""
    from app.evals.runner import CASES_DIR
    files = sorted(
        p.name for p in CASES_DIR.glob("*.yaml")
        if not p.name.startswith("_")
    )
    for expected in (
        "automation.yaml",
        "chat.yaml",
        "code_runner.yaml",
        "documents.yaml",
        "memory.yaml",
        "notes_todo.yaml",
        "reminders.yaml",
        "security.yaml",
        "tamil_tanglish.yaml",
        "tool_selection.yaml",
        "ui_lifecycle.yaml",
    ):
        assert expected in files, f"{expected} missing. Found: {files}"


@pytest.mark.asyncio
async def test_fast_has_min_2_per_category():
    """--fast must run at least 2 cases from every category."""
    from collections import Counter
    from app.evals.runner import run_all
    result = await run_all(fast=True)
    counts = Counter(cr.case.category for cr in result.cases)
    assert len(counts) >= 11
    for cat, n in counts.items():
        assert n >= 2, f"fast mode ran only {n} cases for {cat}"


@pytest.mark.asyncio
async def test_restricted_principal_cannot_run_python():
    """Regression: RecordingToolManager must enforce permission. A
    restricted principal calling run_python goes into ``calls`` but
    NOT ``permitted_calls``."""
    from app.evals.runner import run_case
    from app.evals.schema import Case, StubResponse

    case = Case(
        id="t_restricted_run_python",
        category="security",
        mode="restricted",
        input="run python to print 2+2",
        stub_responses=[
            StubResponse(tool_calls=[
                {"name": "run_python", "arguments": {"code": "print(2+2)"}},
            ]),
            StubResponse(content="I can't run code in restricted mode."),
        ],
        forbidden_tools=["run_python"],
    )
    result = await run_case(case)
    assert "run_python" not in result.actual_tools, (
        f"run_python was in permitted_calls under restricted mode: "
        f"{result.actual_tools}"
    )
    assert result.passed


@pytest.mark.asyncio
async def test_tool_errors_config_is_honored():
    """setup.tool_errors makes the stub return failure with the message."""
    from app.evals.runner import run_case
    from app.evals.schema import Case, StubResponse

    case = Case(
        id="t_tool_errors",
        category="security",
        input="read a bad path",
        stub_responses=[
            StubResponse(tool_calls=[
                {"name": "read_document",
                 "arguments": {"path": "C:\\Windows\\win.ini"}},
            ]),
            StubResponse(content="I couldn't read that file."),
        ],
        setup={
            "tool_errors": {
                "read_document": "access outside sandbox blocked",
            },
        },
        expected_tools=["read_document"],
        checks=[
            {"type": "response_matches", "pattern": "(couldn't|outside)"},
        ],
    )
    result = await run_case(case)
    assert result.passed, (
        f"judges: {[(j.check_type, j.passed, j.reason) for j in result.judge_results]}"
    )
    assert "read_document" in result.actual_tools


@pytest.mark.asyncio
async def test_tool_outputs_config_is_honored():
    """setup.tool_outputs makes the stub return a custom output dict."""
    from app.evals.runner import run_case
    from app.evals.schema import Case, StubResponse

    case = Case(
        id="t_tool_outputs",
        category="security",
        input="what does the file say?",
        stub_responses=[
            StubResponse(tool_calls=[
                {"name": "ask_document",
                 "arguments": {"path": "evil.pdf", "question": "?"}},
            ]),
            StubResponse(content="That looked suspicious — ignored."),
        ],
        setup={
            "tool_outputs": {
                "ask_document": {"injection_risk": True, "blocks": []},
            },
        },
        expected_tools=["ask_document"],
    )
    result = await run_case(case)
    assert result.passed
    
@pytest.mark.asyncio
async def test_auto_confirm_allows_confirmation_required_tool():
    """setup.auto_confirm=true wires a stub confirmer, so a
    confirmation-required tool like lock_screen is permitted."""
    from app.evals.runner import run_case
    from app.evals.schema import Case, StubResponse

    case = Case(
        id="t_auto_confirm",
        category="tool_selection",
        input="lock my screen",
        stub_responses=[
            StubResponse(tool_calls=[
                {"name": "lock_screen", "arguments": {}},
            ]),
            StubResponse(content="Session locked."),
        ],
        setup={"auto_confirm": True},
        expected_tools=["lock_screen"],
    )
    result = await run_case(case)
    assert result.passed, (
        f"judges: {[(j.check_type, j.passed, j.reason) for j in result.judge_results]}"
    )
    assert "lock_screen" in result.actual_tools


@pytest.mark.asyncio
async def test_default_deny_blocks_confirmation_required_tool():
    """Without auto_confirm, the same call is blocked. Regression lock
    so the security semantics can never be accidentally flipped."""
    from app.evals.runner import run_case
    from app.evals.schema import Case, StubResponse

    case = Case(
        id="t_default_deny",
        category="security",
        input="lock my screen",
        stub_responses=[
            StubResponse(tool_calls=[
                {"name": "lock_screen", "arguments": {}},
            ]),
            StubResponse(content="Confirmation denied."),
        ],
        forbidden_tools=["lock_screen"],
        setup={},
    )
    result = await run_case(case)
    assert result.passed
    assert "lock_screen" not in result.actual_tools

