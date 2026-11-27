"""Data classes for eval cases, checks, and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StubResponse:
    """One scripted LLM response in a case."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Check:
    """One deterministic check applied to a case result."""

    type: str                        # see judges.py for types
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Case:
    """One evaluation case, loaded from YAML."""

    id: str
    category: str
    input: str
    mode: str = "creator"            # "creator" | "restricted"
    stub_responses: list[StubResponse] = field(default_factory=list)
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    setup: dict[str, Any] = field(default_factory=dict)   # memories, files, etc.
    notes: str = ""


    def __post_init__(self) -> None:
        """Normalise dict inputs to typed dataclasses.

        YAML loading produces typed Check / StubResponse objects, but
        programmatic construction (tests, notebooks, ad-hoc scripts)
        often passes plain dicts. Accept both — downstream code
        (judges, runner) always sees typed objects.
        """
        if self.checks:
            normalised: list[Check] = []
            for c in self.checks:
                if isinstance(c, dict):
                    normalised.append(Check(
                        type=c["type"],
                        params={k: v for k, v in c.items() if k != "type"},
                    ))
                else:
                    normalised.append(c)
            self.checks = normalised

        if self.stub_responses:
            normalised_sr: list[StubResponse] = []
            for r in self.stub_responses:
                if isinstance(r, dict):
                    normalised_sr.append(StubResponse(
                        content=r.get("content", ""),
                        tool_calls=list(r.get("tool_calls") or []),
                    ))
                else:
                    normalised_sr.append(r)
            self.stub_responses = normalised_sr


@dataclass
class JudgeResult:
    """One check's pass/fail verdict with reason."""

    passed: bool
    reason: str = ""
    check_type: str = ""


@dataclass
class CaseResult:
    """Result of running one case."""

    case: Case
    passed: bool
    judge_results: list[JudgeResult] = field(default_factory=list)
    actual_tools: list[str] = field(default_factory=list)
    response_text: str = ""
    model: str = ""
    provider: str = ""
    duration_ms: float = 0.0
    error: str = ""


@dataclass
class RunResult:
    """Result of one full eval run (all cases or one category)."""

    cases: list[CaseResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    total_duration_ms: float = 0.0
    category: str = "all"

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0