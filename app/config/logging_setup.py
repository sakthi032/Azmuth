# app/config/logging_setup.py
"""Structured logging for Azmuth.

Two output formats are supported:

* :class:`JsonFormatter` — machine readable, used for log files and for
  feeding the desktop UI's activity panel in later steps.
* :class:`HumanFormatter` — compact single-line format for development.

Every record may carry three structured fields, supplied through
``logging.Logger``'s ``extra=`` argument:

    logger.info("opened app", extra={"component": "automation",
                                    "event": "TOOL_COMPLETED",
                                    "task_id": task.id})

Sensitive keys (``api_key``, ``token``, ``password``, …) are redacted before
serialisation. Sensitive user data must not be logged at all — redaction is a
last line of defence, not a licence to log secrets.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Final

from app.config.settings import Settings

#: Attribute names injected by the logging module itself; anything else found
#: on a LogRecord is treated as caller-supplied context.
_STANDARD_ATTRS: Final[frozenset[str]] = frozenset(
    vars(
        logging.LogRecord(
            name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
        )
    )
) | {"message", "asctime"}

_SENSITIVE_MARKERS: Final[tuple[str, ...]] = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "passwd",
    "password",
    "pin",
    "secret",
    "token",
)

_REDACTED: Final[str] = "***redacted***"

_CONFIGURED: bool = False


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def _scrub(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {str(k): _scrub(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_scrub(item, key=key) for item in value]
    return value


class DefaultsFilter(logging.Filter):
    """Guarantees that ``component``/``event``/``task_id`` exist on records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "component"):
            record.component = record.name
        if not hasattr(record, "event"):
            record.event = None
        if not hasattr(record, "task_id"):
            record.task_id = None
        return True


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON object."""

    def __init__(self, *, redact: bool = True) -> None:
        super().__init__()
        self._redact = redact

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            message = str(record.msg)

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "severity": record.levelname,
            "component": getattr(record, "component", record.name),
            "event": getattr(record, "event", None),
            "task_id": getattr(record, "task_id", None),
            "message": message,
        }

        context = {
            key: value
            for key, value in vars(record).items()
            if key not in _STANDARD_ATTRS and key not in {"component", "event", "task_id"}
        }
        if context:
            payload["context"] = _scrub(context) if self._redact else context

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    """Compact, single-line, human-readable format for development."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s [%(component)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def _tag(handler: logging.Handler) -> logging.Handler:
    """Mark a handler as owned by Azmuth so it can be replaced on reload."""
    setattr(handler, "_azmuth_owned", True)
    handler.addFilter(DefaultsFilter())
    return handler


def setup_logging(settings: Settings, *, force: bool = False) -> logging.Logger:
    """Configure the root logger. Safe to call more than once."""
    global _CONFIGURED

    root = logging.getLogger()
    if _CONFIGURED and not force:
        return root

    for handler in list(root.handlers):
        if getattr(handler, "_azmuth_owned", False):
            root.removeHandler(handler)
            handler.close()

    root.setLevel(logging.getLevelName(settings.log_level))

    formatter: logging.Formatter = (
        JsonFormatter() if settings.log_json else HumanFormatter()
    )

    if settings.log_to_console:
        stream_handler = _tag(logging.StreamHandler(stream=sys.stderr))
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    if settings.log_to_file:
        settings.paths.logs.mkdir(parents=True, exist_ok=True)
        file_handler = _tag(
            logging.handlers.RotatingFileHandler(
                settings.paths.logs / "azmuth.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    # Third-party noise control.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    return root


def get_logger(component: str) -> logging.Logger:
    """Return the logger for a named component, e.g. ``get_logger("memory")``."""
    return logging.getLogger(f"azmuth.{component}")


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    event: str | None = None,
    task_id: str | None = None,
    **context: Any,
) -> None:
    """Emit a structured log record with optional context fields."""
    extra: dict[str, Any] = dict(context)
    if event is not None:
        extra["event"] = event
    if task_id is not None:
        extra["task_id"] = task_id
    logger.log(level, message, extra=extra)