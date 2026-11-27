"""Safety tests proving evals never touch real data, DBs, or user files."""

from __future__ import annotations

import pytest

from app.evals.runner import run_case
from app.evals.schema import Case, StubResponse


@pytest.mark.asyncio
async def test_evals_do_not_touch_real_data_dir(tmp_path, monkeypatch):
    """Running an eval case must not write anything to a real data directory."""
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    monkeypatch.setenv("AZMUTH_DATA_DIR", str(real_data))

    case = Case(
        id="safety_memory_isolation",
        category="memory",
        input="my name is SafetyTestUser",
        setup={"memories": [{"content": "Secret: do_not_leak", "kind": "fact"}]},
        stub_responses=[StubResponse(content="Nice to meet you!")],
    )
    result = await run_case(case)
    assert result.passed is True
    assert list(real_data.iterdir()) == [], (
        f"Eval touched real_data dir: {list(real_data.iterdir())}"
    )
