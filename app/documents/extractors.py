"""File-to-text extraction for PDF, DOCX, XLSX, CSV, TXT, MD.

Each format maps to one extractor that returns ``ExtractedBlock`` items —
one per page (PDF), per sheet (XLSX), or a single Body block (text
formats). Images are handled separately by :mod:`app.documents.image_extract`.

The extraction libraries (``pypdf``, ``python-docx``, ``openpyxl``) are
optional imports. A missing library raises a clear message naming the
``pip install`` command; the rest of the pipeline still boots.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from app.config.logging_setup import get_logger

logger = get_logger("documents.extractors")

#: Hard cap on extracted characters per document, regardless of format.
MAX_DOC_CHARS = 200_000

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------
try:
    from pypdf import PdfReader  # type: ignore[import-untyped]
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    from docx import Document as _DocxDocument  # type: ignore[import-untyped]
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    from openpyxl import load_workbook  # type: ignore[import-untyped]
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


@dataclass
class ExtractedBlock:
    """One page, sheet, or logical body block."""

    index: int
    label: str      # "Page 1", "Sheet: Sales", "Body"
    text: str


@dataclass
class ExtractedDocument:
    path: Path
    mime: str
    blocks: list[ExtractedBlock] = field(default_factory=list)
    truncated: bool = False

    @property
    def full_text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


# ---------------------------------------------------------------------------
# Format extractors
# ---------------------------------------------------------------------------
def _ext_pdf(path: Path) -> tuple[str, list[ExtractedBlock]]:
    if not HAS_PYPDF:
        raise RuntimeError(
            "PDF support requires pypdf — pip install pypdf"
        )
    reader = PdfReader(str(path))
    blocks: list[ExtractedBlock] = []
    total = 0
    truncated = False
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if total + len(text) > MAX_DOC_CHARS:
            text = text[: MAX_DOC_CHARS - total]
            truncated = True
        blocks.append(ExtractedBlock(index=i, label=f"Page {i}", text=text))
        total += len(text)
        if truncated:
            break
    return "application/pdf", blocks, truncated


def _ext_docx(path: Path) -> tuple[str, list[ExtractedBlock], bool]:
    if not HAS_DOCX:
        raise RuntimeError(
            "DOCX support requires python-docx — pip install python-docx"
        )
    doc = _DocxDocument(str(path))
    parts: list[str] = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    text = "\n".join(parts)
    truncated = len(text) > MAX_DOC_CHARS
    if truncated:
        text = text[:MAX_DOC_CHARS]
    mime = (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    )
    return mime, [ExtractedBlock(1, "Body", text)], truncated


def _ext_xlsx(path: Path) -> tuple[str, list[ExtractedBlock], bool]:
    if not HAS_OPENPYXL:
        raise RuntimeError(
            "XLSX support requires openpyxl — pip install openpyxl"
        )
    wb = load_workbook(str(path), read_only=True, data_only=True)
    blocks: list[ExtractedBlock] = []
    total = 0
    truncated = False
    try:
        for idx, name in enumerate(wb.sheetnames):
            ws = wb[name]
            lines: list[str] = []
            for row in ws.iter_rows(values_only=True):
                line = " | ".join(
                    "" if c is None else str(c) for c in row
                )
                if line.strip():
                    lines.append(line)
            text = "\n".join(lines)
            if total + len(text) > MAX_DOC_CHARS:
                text = text[: MAX_DOC_CHARS - total]
                truncated = True
            blocks.append(
                ExtractedBlock(idx, f"Sheet: {name}", text)
            )
            total += len(text)
            if truncated:
                break
    finally:
        wb.close()
    mime = (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    )
    return mime, blocks, truncated

def _ext_text_generic(path: Path) -> tuple[str, list[ExtractedBlock], bool]:
    """Fallback for any text-like file: source code, config, logs, …."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(8192)
            if b"\x00" in head:
                raise RuntimeError(
                    f"{path.name} appears to be a binary file, not text"
                )
            f.seek(0)
            raw = f.read(_MAX_RAW_BYTES)
    except OSError as exc:
        raise RuntimeError(f"could not read {path}: {exc}") from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    truncated = size > _MAX_RAW_BYTES or len(text) > MAX_DOC_CHARS
    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS]

    return "text/plain", [ExtractedBlock(1, "Body", text)], truncated


def _ext_csv(path: Path) -> tuple[str, list[ExtractedBlock], bool]:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="utf-8", errors="replace")
    reader = csv.reader(io.StringIO(raw))
    lines = [
        " | ".join(row) for row in reader
        if any(cell.strip() for cell in row)
    ]
    text = "\n".join(lines)
    truncated = len(text) > MAX_DOC_CHARS
    if truncated:
        text = text[:MAX_DOC_CHARS]
    return "text/csv", [ExtractedBlock(1, "Body", text)], truncated


_EXT_MAP = {
    ".pdf": _ext_pdf,
    ".docx": _ext_docx,
    ".xlsx": _ext_xlsx,
    ".csv": _ext_csv,
}

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
# Binary formats — never attempt text extraction. Container /
# executable / media formats where "reading text" is meaningless.
_BINARY_EXTS = {
    # Executables & libraries
    ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".jar",
    ".pyc", ".pyo", ".o", ".a", ".lib", ".obj", ".pdb", ".msi",
    # Archives
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".iso", ".dmg", ".cab",
    # Audio / video
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus",
    ".wma", ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm",
    ".flv", ".m4v", ".mpg", ".mpeg",
    # Non-vision images
    ".ico", ".tiff", ".tif", ".psd", ".raw",
    # Structured binary data
    ".db", ".sqlite", ".sqlite3", ".mdb", ".pkl", ".pickle",
    ".npy", ".npz", ".parquet", ".feather", ".h5", ".hdf5",
    ".pt", ".pth", ".onnx", ".model",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
}

# Cap on how many raw bytes we read from disk. Enough for 200 K chars
# in UTF-8 plus headroom, without loading 100 MB files into memory.
_MAX_RAW_BYTES = 2 * 1024 * 1024  # 2 MB

def is_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTS


def is_supported(path: Path) -> bool:
    """True for any file we can produce text from.

    Includes: PDF, DOCX, XLSX, CSV, images (via VLM), and every
    UTF-8-readable format — source code, config, logs, JSON, YAML,
    TOML, INI, HTML, XML, SQL, Markdown, plain text, and files with
    no extension (Makefile, Dockerfile, …).

    Returns False only for binary formats where text extraction is
    meaningless (exe, zip, mp3, …). ``extract_document`` still
    re-checks the null-byte signature as a defence in depth.
    """
    return path.suffix.lower() not in _BINARY_EXTS


def extract_document(path: Path) -> ExtractedDocument:
    """Extract text blocks from ``path``. Images are handled elsewhere.

    Raises ``ValueError`` for binary files where text extraction is
    not applicable. Any UTF-8-readable file works.
    """
    if is_image(path):
        raise ValueError(
            f"{path.name} is an image — use app.documents.image_extract"
        )
    ext = path.suffix.lower()
    if ext in _BINARY_EXTS:
        raise ValueError(
            f"{path.name} is a binary file ({ext or 'no extension'}) "
            "and cannot be read as text"
        )

    func = _EXT_MAP.get(ext)
    if func is not None:
        mime, blocks, truncated = func(path)
    else:
        mime, blocks, truncated = _ext_text_generic(path)

    logger.info(
        "document extracted",
        extra={
            "event": "DOCUMENT_EXTRACTED",
            "path": str(path),
            "mime": mime,
            "blocks": len(blocks),
            "truncated": truncated,
        },
    )
    return ExtractedDocument(
        path=path, mime=mime, blocks=blocks, truncated=truncated,
    )