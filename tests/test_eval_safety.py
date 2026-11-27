"""Safety tests proving evals never touch real data, DBs, or user files."""

from __future__ import annotations

import pytest

from app.evals.runner import run_all
from app.evals.report import RESULTS_DIR


@pytest.mark.asyncio
async def test_eval_suite_does_not_touch_sentinel_data_dir(tmp_path, monkeypatch):
    """Running the eval suite with real data paths pointed at a sentinel
    directory must leave the sentinel directory completely untouched."""
    sentinel = tmp_path / "sentinel_data"
    sentinel.mkdir()
    marker = sentinel / "do_not_modify.txt"
    marker.write_text("original", encoding="utf-8")

    for env_var in (
        "AZMUTH_DATA_DIR",
        "AZMUTH_MEMORY_DIR",
        "AZMUTH_LOGS_DIR",
        "AZMUTH_WORKSPACE",
    ):
        monkeypatch.setenv(env_var, str(sentinel))

    result = await run_all(fast=True)
    assert result.total >= 22
    assert result.passed == result.total

    entries = sorted(p.name for p in sentinel.iterdir())
    assert entries == ["do_not_modify.txt"], f"Eval touched sentinel dir: {entries}"
    assert marker.read_text(encoding="utf-8") == "original"
    assert RESULTS_DIR.exists()
