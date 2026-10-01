# app/config/__init__.py
"""Configuration and logging setup."""

from app.config.logging_setup import get_logger, log_event, setup_logging
from app.config.settings import PRIMARY_LANGUAGES, Paths, Settings, build_paths, load_settings

__all__ = [
    "PRIMARY_LANGUAGES",
    "Paths",
    "Settings",
    "build_paths",
    "get_logger",
    "load_settings",
    "log_event",
    "setup_logging",
]