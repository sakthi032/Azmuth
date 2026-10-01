# tests/test_logging.py
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config.logging_setup import JsonFormatter, get_logger, setup_logging
from app.config.settings import load_settings


def _settings(tmp_path: Path, **env: str):
    base = {
        "AZMUTH_HOME": str(tmp_path),
        "AZMUTH_LOG_JSON": "true",
        "AZMUTH_LOG_TO_CONSOLE": "false",
        "AZMUTH_LOG_TO_FILE": "true",
        "AZMUTH_LOG_LEVEL": "DEBUG",
    }
    base.update(env)
    return load_settings(environ=base, env_file=tmp_path / "missing.env")


def _record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="azmuth.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_structured_fields() -> None:
    payload = json.loads(
        JsonFormatter().format(
            _record(component="memory", event="MEMORY_STORED", task_id="task-1")
        )
    )

    assert payload["component"] == "memory"
    assert payload["event"] == "MEMORY_STORED"
    assert payload["task_id"] == "task-1"
    assert payload["message"] == "hello world"
    assert payload["severity"] == "INFO"
    assert "timestamp" in payload


def test_json_formatter_redacts_sensitive_context() -> None:
    payload = json.loads(
        JsonFormatter().format(_record(api_key="abc123", safe="ok"))
    )

    assert payload["context"]["api_key"] == "***redacted***"
    assert payload["context"]["safe"] == "ok"
    
def test_json_formatter_redacts_nested_sensitive_values() -> None:
    payload = json.loads(
        JsonFormatter().format(
            _record(nested={"token": "xyz", "note": "ok"})
        )
    )

    assert payload["context"]["nested"]["token"] == "***redacted***"
    assert payload["context"]["nested"]["note"] == "ok"


def test_setup_logging_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    setup_logging(settings, force=True)
    first = len(logging.getLogger().handlers)
    setup_logging(settings)
    second = len(logging.getLogger().handlers)

    assert first == second


def test_logging_writes_json_lines_to_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    setup_logging(settings, force=True)

    logger = get_logger("test")
    logger.info("file check", extra={"event": "TEST_EVENT"})

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = settings.paths.logs / "azmuth.log"
    assert log_file.is_file()

    lines = [line for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = [json.loads(line) for line in lines]
    assert any(entry["message"] == "file check" for entry in entries)
    assert any(entry.get("event") == "TEST_EVENT" for entry in entries)