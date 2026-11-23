"""B1 document extractors and tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.documents.extractors import extract_document, is_supported
from app.security.workspace import ProjectWorkspacePolicy
from app.tools.builtin.documents import AskDocumentTool, ReadDocumentTool


@pytest.fixture
def workspace(tmp_path: Path) -> ProjectWorkspacePolicy:
    return ProjectWorkspacePolicy.from_paths([tmp_path])


def test_supported_extensions():
    assert is_supported(Path("x.pdf"))
    assert is_supported(Path("x.DOCX"))
    assert is_supported(Path("x.xlsx"))
    assert is_supported(Path("x.csv"))
    assert is_supported(Path("x.md"))
    assert is_supported(Path("x.png"))
    assert not is_supported(Path("x.bin"))


def test_extract_txt(tmp_path: Path):
    p = tmp_path / "notes.txt"
    p.write_text("Line one.\nLine two.\n", encoding="utf-8")
    doc = extract_document(p)
    assert len(doc.blocks) == 1
    assert "Line one." in doc.blocks[0].text
    assert doc.blocks[0].label == "Body"


def test_extract_md(tmp_path: Path):
    p = tmp_path / "readme.md"
    p.write_text("# Title\n\nBody text.", encoding="utf-8")
    doc = extract_document(p)
    assert "# Title" in doc.full_text


def test_extract_csv(tmp_path: Path):
    p = tmp_path / "data.csv"
    p.write_text("name,score\nAlice,10\nBob,20\n", encoding="utf-8")
    doc = extract_document(p)
    assert "Alice | 10" in doc.full_text
    assert "Bob | 20" in doc.full_text


def test_extract_docx(tmp_path: Path):
    pytest.importorskip("docx")
    from docx import Document as Docx
    p = tmp_path / "note.docx"
    d = Docx()
    d.add_paragraph("Hello from DOCX")
    d.save(str(p))
    doc = extract_document(p)
    assert "Hello from DOCX" in doc.full_text


def test_extract_xlsx(tmp_path: Path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook
    p = tmp_path / "book.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Region", "Qty"])
    ws.append(["East", 100])
    wb.save(str(p))
    doc = extract_document(p)
    assert doc.blocks[0].label == "Sheet: Sales"
    assert "East | 100" in doc.blocks[0].text


def test_extract_pdf_via_mock(tmp_path: Path):
    """Mock PdfReader so the test doesn't need a real PDF binary."""
    pytest.importorskip("pypdf")
    from app.documents import extractors

    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text
        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, _path: str) -> None:
            self.pages = [FakePage("Page one text."),
                          FakePage("Page two text.")]

    p = tmp_path / "fake.pdf"
    p.write_bytes(b"%PDF-1.4 placeholder")
    with patch.object(extractors, "PdfReader", FakeReader):
        doc = extract_document(p)
    assert len(doc.blocks) == 2
    assert doc.blocks[0].label == "Page 1"
    assert "Page two text." in doc.blocks[1].text


@pytest.mark.asyncio
async def test_read_document_happy_path(tmp_path: Path, workspace):
    p = tmp_path / "note.txt"
    p.write_text("hello world", encoding="utf-8")
    tool = ReadDocumentTool(workspace_policy=workspace)
    result = await tool.execute(path=str(p))
    assert result["file"] == "note.txt"
    assert "hello world" in result["text"]
    assert result["injection_risk"] is False


@pytest.mark.asyncio
async def test_read_document_rejects_outside_workspace(
    tmp_path: Path, workspace
):
    outside = tmp_path.parent / "outside.txt"
    try:
        outside.write_text("private", encoding="utf-8")
        tool = ReadDocumentTool(workspace_policy=workspace)
        result = await tool.execute(path=str(outside))
        assert "error" in result
        assert "outside the workspace" in result["error"]
    finally:
        try:
            outside.unlink()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_ask_document_returns_relevant_block(
    tmp_path: Path, workspace
):
    p = tmp_path / "guide.md"
    p.write_text(
        "# Installation\n\nRun pip install.\n\n"
        "# Usage\n\nCall the function.\n",
        encoding="utf-8",
    )
    tool = AskDocumentTool(workspace_policy=workspace)
    result = await tool.execute(
        path=str(p), question="how do I install this"
    )
    assert result["file"] == "guide.md"
    assert result["blocks"]
    # The installation block should be first because it has the most
    # matching terms
    assert "Installation" in result["blocks"][0]["text"]


@pytest.mark.asyncio
async def test_ask_document_image_uses_vision(tmp_path: Path, workspace):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")

    with patch(
        "app.tools.builtin.documents.extract_image",
        return_value=__import__(
            "app.documents.extractors", fromlist=["ExtractedDocument"]
        ).ExtractedDocument(
            path=img,
            mime="image/*",
            blocks=[__import__(
                "app.documents.extractors", fromlist=["ExtractedBlock"]
            ).ExtractedBlock(1, "Image", "a cat on a windowsill")],
        ),
    ):
        tool = AskDocumentTool(workspace_policy=workspace)
        result = await tool.execute(
            path=str(img), question="what is in this image"
        )
    assert result["blocks"]
    assert "cat" in result["blocks"][0]["text"]
    
def test_extract_python_source(tmp_path: Path):
    p = tmp_path / "main.py"
    p.write_text("def main():\n    print('hi')\n", encoding="utf-8")
    doc = extract_document(p)
    assert "def main()" in doc.full_text


def test_extract_json(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text('{"name": "azmuth", "version": 3}', encoding="utf-8")
    doc = extract_document(p)
    assert "azmuth" in doc.full_text


def test_extract_yaml(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("name: azmuth\nversion: 3\n", encoding="utf-8")
    doc = extract_document(p)
    assert "azmuth" in doc.full_text


def test_extract_html(tmp_path: Path):
    p = tmp_path / "index.html"
    p.write_text(
        "<html><body><h1>Hi</h1></body></html>", encoding="utf-8",
    )
    doc = extract_document(p)
    assert "Hi" in doc.full_text


def test_extract_log(tmp_path: Path):
    p = tmp_path / "app.log"
    p.write_text("2026-11-19 INFO started\n", encoding="utf-8")
    doc = extract_document(p)
    assert "started" in doc.full_text


def test_extract_extensionless(tmp_path: Path):
    p = tmp_path / "Makefile"
    p.write_text("all:\n\t@echo done\n", encoding="utf-8")
    doc = extract_document(p)
    assert "all:" in doc.full_text


def test_binary_exe_rejected(tmp_path: Path):
    p = tmp_path / "app.exe"
    p.write_bytes(b"MZ\x90\x00" + b"\x00" * 200)
    with pytest.raises(ValueError, match="binary"):
        extract_document(p)


def test_binary_zip_rejected(tmp_path: Path):
    p = tmp_path / "data.zip"
    p.write_bytes(b"PK\x03\x04")
    with pytest.raises(ValueError, match="binary"):
        extract_document(p)


def test_binary_disguised_as_txt_rejected(tmp_path: Path):
    """A null-byte-containing file with a text extension is caught by
    signature sniffing, not just the extension check."""
    p = tmp_path / "fake.txt"
    p.write_bytes(b"hello\x00world")
    with pytest.raises(RuntimeError, match="binary"):
        extract_document(p)


def test_is_supported_covers_source_and_config():
    assert is_supported(Path("x.py"))
    assert is_supported(Path("x.js"))
    assert is_supported(Path("x.tsx"))
    assert is_supported(Path("x.java"))
    assert is_supported(Path("x.rs"))
    assert is_supported(Path("x.go"))
    assert is_supported(Path("x.json"))
    assert is_supported(Path("x.yaml"))
    assert is_supported(Path("x.toml"))
    assert is_supported(Path("x.log"))
    assert is_supported(Path("Makefile"))      # extensionless
    assert is_supported(Path("x.html"))
    assert is_supported(Path("x.xml"))
    assert is_supported(Path("x.png"))
    assert is_supported(Path("x.pdf"))
    # Binary formats must still return False
    assert not is_supported(Path("x.exe"))
    assert not is_supported(Path("x.dll"))
    assert not is_supported(Path("x.zip"))
    assert not is_supported(Path("x.mp3"))
    assert not is_supported(Path("x.pyc"))


@pytest.mark.asyncio
async def test_read_document_python_via_tool(tmp_path: Path, workspace):
    p = tmp_path / "main.py"
    p.write_text("print('hello')\n", encoding="utf-8")
    tool = ReadDocumentTool(workspace_policy=workspace)
    result = await tool.execute(path=str(p))
    assert "error" not in result
    assert "print('hello')" in result["text"]
    assert result["file"] == "main.py"


@pytest.mark.asyncio
async def test_ask_document_python_via_tool(tmp_path: Path, workspace):
    p = tmp_path / "app.py"
    p.write_text(
        "def create_user(name):\n    return {'name': name}\n"
        "\n"
        "def delete_user(uid):\n    return True\n",
        encoding="utf-8",
    )
    tool = AskDocumentTool(workspace_policy=workspace)
    result = await tool.execute(
        path=str(p), question="how do I create a user",
    )
    assert "error" not in result
    assert result["blocks"]
    assert "create_user" in result["blocks"][0]["text"]


@pytest.mark.asyncio
async def test_read_document_binary_returns_clear_error(
    tmp_path: Path, workspace
):
    p = tmp_path / "installer.exe"
    p.write_bytes(b"MZ\x90\x00" + b"\x00" * 200)
    tool = ReadDocumentTool(workspace_policy=workspace)
    result = await tool.execute(path=str(p))
    assert "error" in result
    assert "binary" in result["error"]

