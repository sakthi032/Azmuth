"""Document reading and Q&A tools.

Files must resolve inside an approved workspace root. There is no
session bypass in this phase; the file picker (B3) will add one.

``read_document`` returns text extracted from the file, capped per block.
``ask_document`` scores each block against the question and returns the
top blocks — the LLM (which called the tool) synthesises the answer.
B2 will replace keyword scoring with hybrid Chroma+BM25 retrieval.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar
from app.documents.session_files import (
    SessionFileRegistry, get_registry,
)
from app.config.logging_setup import get_logger
from app.documents.extractors import (
    ExtractedDocument,
    extract_document,
    is_image,
    is_supported,
)
from app.documents.image_extract import extract_image
from app.security.capabilities import Capability
from app.security.workspace import ProjectWorkspacePolicy
from app.tools.base import RiskLevel, Tool

logger = get_logger("tools.documents")

# Mirrors app/search/extractor.py patterns. B4 will factor this into a
# shared module; keeping it inline here makes B1 self-contained.
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"you\s+are\s+now\s+a",
        r"new\s+system\s+prompt",
        r"disregard\s+(your|the)\s+(previous|above)",
        r"act\s+as\s+(if\s+you\s+are|a\s+different)",
        r"forget\s+everything",
    ]
]

_BLOCK_CAP = 20_000
_TOTAL_CAP = 60_000

def _citation_for(filename: str, label: str) -> str:
    if label.startswith("Page "):
        return f"(source: {filename}, p.{label[len('Page '):]})"
    if label.startswith("Sheet: "):
        return f"(source: {filename}, sheet {label[len('Sheet: '):]})"
    return f"(source: {filename})"

def _scan_injection(text: str) -> bool:
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return True
    return False


def _resolve_path(
    raw: str,
    workspace: ProjectWorkspacePolicy,
    session_files: SessionFileRegistry | None = None,
) -> Path:
    try:
        candidate = Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PermissionError(f"could not resolve {raw!r}: {exc}") from exc
    if workspace.is_in_workspace(candidate):
        return candidate
    registry = session_files if session_files is not None else get_registry()
    if registry.is_allowed(candidate):
        return candidate
    raise PermissionError(
        f"{candidate} is outside the workspace sandbox and was not "
        f"selected through the file picker"
    )


async def _extract_any(path: Path) -> ExtractedDocument:
    if is_image(path):
        return await extract_image(path)
    return extract_document(path)


class ReadDocumentTool(Tool):
    """Extract readable text from a file inside the workspace sandbox."""

    name: ClassVar[str] = "read_document"
    description: ClassVar[str] = (
        "Extract readable text from ANY file inside the workspace — "
        "source code (.py, .js, .ts, .java, .c, .cpp, .rs, .go, .rb, "
        ".php, .swift, .kt, .lua, .sh, .ps1, .sql, …), config (.json, "
        ".yaml, .toml, .ini, .conf, .properties, .env, .xml, .html, "
        ".css, …), logs, CSV, Markdown, plain text, PDF, DOCX, XLSX, "
        "or images (images via vision). Returns the text plus a "
        "per-page / per-sheet breakdown. Binary files (.exe, .zip, "
        ".mp3, .dll, .pyc, …) are rejected with a clear message. Use "
        "this when the user says 'read this file' or 'what does this "
        "file say'. For a question about a file's contents, prefer "
        "ask_document."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "Absolute or workspace-relative file path.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 100000,
                "default": 30000,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.READ_PERSONAL_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(
        self,
        *,
        workspace_policy: ProjectWorkspacePolicy,
        session_files: SessionFileRegistry | None = None,
    ) -> None:
        self._workspace = workspace_policy
        self._session_files = session_files

    async def execute(
        self, *, path: str, max_chars: int = 30_000
    ) -> dict[str, Any]:
        try:
            resolved = _resolve_path(
                path, self._workspace, self._session_files,
            )
        except PermissionError as exc:
            return {"error": str(exc), "path": path}
        if not resolved.is_file():
            return {"error": f"{resolved} is not a file", "path": str(resolved)}
        if not is_supported(resolved):
            return {
                "error": (
                    f"{resolved.name} is a binary file "
                    f"({resolved.suffix or 'no extension'}) and cannot "
                    f"be read as text"
                ),
                "path": str(resolved),
            }
        try:
            doc = await _extract_any(resolved)
        except Exception as exc:
            logger.exception(
                "document extraction failed",
                extra={"event": "DOCUMENT_EXTRACT_FAILED",
                       "path": str(resolved)},
            )
            return {"error": str(exc), "path": str(resolved)}

        full_text = doc.full_text
        truncated = len(full_text) > max_chars
        text = full_text[:max_chars]
        return {
            "file": resolved.name,
            "path": str(resolved),
            "mime": doc.mime,
            "page_count": len(doc.blocks),
            "blocks": [
                {
                    "label": b.label,
                    "citation": _citation_for(resolved.name, b.label),
                    "text": b.text[:_BLOCK_CAP],
                }
                for b in doc.blocks
            ],
            "text": text,
            "truncated": truncated or doc.truncated,
            "injection_risk": _scan_injection(full_text),
        }


class AskDocumentTool(Tool):
    """Answer a question about a file using keyword-scored block retrieval."""

    name: ClassVar[str] = "ask_document"
    description: ClassVar[str] = (
        "Answer a question about ANY file inside the workspace — "
        "source code, config, logs, JSON, YAML, HTML, CSV, Markdown, "
        "plain text, PDF, DOCX, XLSX, or images (images via vision). "
        "Returns the most relevant pages / sheets / paragraphs from "
        "the file (with their labels), so you can synthesise the "
        "answer. Cite the file name and the page or sheet label in "
        "your reply."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
            },
            "question": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1000,
            },
        },
        "required": ["path", "question"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.READ_PERSONAL_FILE
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(
        self,
        *,
        workspace_policy: ProjectWorkspacePolicy,
        session_files: SessionFileRegistry | None = None,
    ) -> None:
        self._workspace = workspace_policy
        self._session_files = session_files

    async def execute(
        self, *, path: str, question: str
    ) -> dict[str, Any]:
        try:
            resolved = _resolve_path(
                path, self._workspace, self._session_files,
            )
        except PermissionError as exc:
            return {"error": str(exc), "path": path}
        if not resolved.is_file():
            return {"error": f"{resolved} is not a file"}
        if not is_supported(resolved):
            return {
                "error": (
                    f"{resolved.name} is a binary file "
                    f"({resolved.suffix or 'no extension'}) and cannot "
                    f"be read as text"
                ),
            }
        try:
            doc = await _extract_any(resolved)
        except Exception as exc:
            return {"error": str(exc), "path": str(resolved)}

        terms = {
            t for t in re.findall(r"[a-z0-9]+", question.lower())
            if len(t) > 2
        }
        scored: list[tuple[int, Any]] = []
        for block in doc.blocks:
            text_lower = block.text.lower()
            hits = sum(1 for t in terms if t in text_lower)
            if hits:
                scored.append((hits, block))
        scored.sort(key=lambda x: -x[0])

        top = scored[:5] if scored else [
            (0, b) for b in doc.blocks[:3]
        ]
        total_chars = 0
        blocks_out: list[dict[str, Any]] = []
        for _score, block in top:
            budget = min(_BLOCK_CAP, _TOTAL_CAP - total_chars)
            if budget <= 0:
                break
            piece = block.text[:budget]
            total_chars += len(piece)
            # Canonical citation — Page N -> "p.N", Sheet: X -> "sheet X"
            if block.label.startswith("Page "):
                citation = (
                    f"(source: {resolved.name}, "
                    f"p.{block.label[len('Page '):]})"
                )
            elif block.label.startswith("Sheet: "):
                citation = (
                    f"(source: {resolved.name}, "
                    f"sheet {block.label[len('Sheet: '):]})"
                )
            else:
                citation = f"(source: {resolved.name})"
            blocks_out.append({
                "label": block.label,
                "citation": citation,
                "text": piece,
            })

        return {
            "file": resolved.name,
            "question": question,
            "blocks": blocks_out,
            "injection_risk": _scan_injection(doc.full_text),
            "note": (
                "B1 uses keyword scoring over per-block text. B2 will "
                "upgrade to hybrid Chroma+BM25 retrieval with precise "
                "citations."
            ),
        }


__all__ = ["AskDocumentTool", "ReadDocumentTool"]