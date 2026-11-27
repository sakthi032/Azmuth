"""Unit tests for deterministic judges."""

from __future__ import annotations

from app.evals.judges import judge
from app.evals.schema import Case, CaseResult, Check


def _case() -> Case:
    return Case(id="t", category="chat", input="x")


def _result(text: str = "", tools: list[str] | None = None) -> CaseResult:
    return CaseResult(
        case=_case(),
        passed=False,
        response_text=text,
        actual_tools=tools or [],
    )


def test_tool_called_pass_and_fail():
    c = Check(type="tool_called", params={"tool": "get_weather"})
    assert judge(c, _case(), _result(tools=["get_weather"])).passed
    assert not judge(c, _case(), _result(tools=["search_web"])).passed


def test_tool_not_called():
    c = Check(type="tool_not_called", params={"tool": "run_python"})
    assert judge(c, _case(), _result(tools=[])).passed
    assert not judge(c, _case(), _result(tools=["run_python"])).passed


def test_response_matches():
    c = Check(type="response_matches", params={"pattern": r"\d+"})
    assert judge(c, _case(), _result("there are 42")).passed
    assert not judge(c, _case(), _result("no numbers")).passed


def test_response_not_matches():
    c = Check(type="response_not_matches",
              params={"pattern": "<spoken>"})
    assert judge(c, _case(), _result("clean text")).passed
    assert not judge(c, _case(), _result("x <spoken> y")).passed


def test_no_template_label_leak():
    c = Check(type="no_template_label_leak")
    assert judge(c, _case(), _result("clean answer")).passed
    assert not judge(c, _case(),
                     _result("Display (full detail) then foo")).passed
    assert not judge(c, _case(),
                     _result("[Detailed answer markdown]")).passed


def test_no_runner_lines():
    c = Check(type="response_no_runner_lines")
    assert judge(c, _case(), _result("just output")).passed
    assert not judge(c, _case(),
                     _result("[runner] start\n[runner] user code")).passed


def test_script_mixing():
    c = Check(type="response_no_script_mixing")
    assert judge(c, _case(),
                 _result("இது சுத்தமான தமிழ்")).passed
    # Mixed token: Tamil + Latin in one word
    assert not judge(c, _case(),
                     _result("செய்work")).passed


def test_has_citation():
    c = Check(type="response_has_citation")
    assert judge(c, _case(),
                 _result("see (source: file.pdf, p.4)")).passed
    assert not judge(c, _case(), _result("see file.pdf page 4")).passed


def test_json_schema_valid():
    c = Check(type="json_schema_valid")
    assert judge(c, _case(), _result('{"a": 1}')).passed
    assert judge(c, _case(), _result('[1, 2, 3]')).passed
    assert not judge(c, _case(), _result('not json')).passed


def test_unknown_check_fails():
    c = Check(type="some_future_check", params={})
    assert not judge(c, _case(), _result()).passed