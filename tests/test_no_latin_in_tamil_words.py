"""Fail if any Tamil word in the project's prompt/identity files
contains Latin letters mixed inside the same word (e.g. ஆஸ்மật)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

_SCAN_FILES = [
    _ROOT / "app" / "brain" / "prompts.py",
    _ROOT / "app" / "brain" / "language_guard.py",
]
_SCAN_DIRS = [
    _ROOT / "data" / "identity",
    _ROOT / "app" / "voice" / "tts",
]

_TAMIL = re.compile(r"[\u0B80-\u0BFF]")
_LATIN = re.compile(r"[A-Za-z]")
_SPLIT = re.compile(r"[\s,.;:!?\"'()\[\]{}<>/=+\\|`]+")


def _iter_text_files():
    for path in _SCAN_FILES:
        if path.is_file():
            yield path
    for root in _SCAN_DIRS:
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in {".md", ".txt", ".py"}:
                    yield path


def _offending_words(content: str) -> list[str]:
    """Words that begin with Tamil and also contain Latin chars."""
    hits: list[str] = []
    for token in _SPLIT.split(content):
        if not token:
            continue
        if not _TAMIL.match(token[0]):
            continue
        if _LATIN.search(token):
            hits.append(token)
    return hits


@pytest.mark.parametrize(
    "path", list(_iter_text_files()) or [pytest.param(None, marks=pytest.mark.skip)],
)
def test_no_tamil_word_contains_latin(path):
    if path is None:
        pytest.skip("no prompt/identity files found")
    content = path.read_text(encoding="utf-8", errors="replace")
    hits = _offending_words(content)
    assert not hits, (
        f"{path.relative_to(_ROOT)} contains Tamil words with Latin "
        f"characters embedded: {hits[:10]} — replace each with the "
        f"canonical Tamil form (e.g. ஆஸ்மத், not ஆஸ்மật)."
    )