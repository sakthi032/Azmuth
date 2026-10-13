
"""The conversation view with markdown rendering.

Renders the transcript with:

* speaker names in bold, color-coded per role
* markdown: headers, bold, italic, inline code, lists, block quotes
* fenced code blocks with a click-to-copy header bar
* a dim meta line (time . language . source) under each message

The markdown parser is deliberately minimal. It handles what a chat LLM
actually produces and nothing more. Anything unrecognized is inserted
as plain text.
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from app.ui.state import Speaker, UIState


# --------------------------------------------------------------------------
# Markdown patterns
# --------------------------------------------------------------------------
_FENCE_OPEN = re.compile(r"^```(\w*)\s*$")
_HEADER = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.+)$")
_NUMBERED = re.compile(r"^(\s*)(\d+)\.\s+(.+)$")
_QUOTE = re.compile(r"^>\s?(.*)$")

_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<!\w)\*(\S(?:[^*\n]*?\S)?)\*(?!\w)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


# --------------------------------------------------------------------------
# Palette - Catppuccin Mocha (comfortable dark, good contrast)
# --------------------------------------------------------------------------
_BG = "#1e1e2e"
_FG = "#cdd6f4"
_DIM = "#6c7086"
_BLUE = "#89b4fa"
_GREEN = "#a6e3a1"
_ORANGE = "#fab387"
_RED = "#f38ba8"
_PURPLE = "#cba6f7"
_PINK = "#f5c2e7"
_YELLOW = "#f9e2af"
_CODE_BG = "#181825"
_CODE_HOVER = "#313244"
_INLINE_BG = "#313244"


class TranscriptView(ttk.Frame):
    """Scrollable conversation transcript."""

    def __init__(self, parent: tk.Widget, state: UIState) -> None:
        super().__init__(parent)
        self._state = state
        self._last_rendered_id = 0
        self._block_counter = 0
        self._code_blocks: dict[str, str] = {}

        self._text = tk.Text(
            self,
            wrap="word",
            state="disabled",
            background=_BG,
            foreground=_FG,
            insertbackground=_FG,
            selectbackground="#45475a",
            borderwidth=0,
            highlightthickness=0,
            font=("Segoe UI", 11),
            padx=14,
            pady=10,
        )
        scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self._text.yview
        )
        self._text.configure(yscrollcommand=scrollbar.set)

        self._text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._configure_tags()
        self._render_all()
        self._unsubscribe = state.subscribe(self._on_state_changed)

    def destroy(self) -> None:
        try:
            self._unsubscribe()
        except Exception:  # noqa: BLE001
            pass
        super().destroy()

    # -- tags -------------------------------------------------------------
    def _configure_tags(self) -> None:
        t = self._text

        # Speaker names - bold, colored, larger
        t.tag_configure("name_user", foreground=_BLUE,
                        font=("Segoe UI", 12, "bold"),
                        spacing1=14, spacing3=4)
        t.tag_configure("name_azmuth", foreground=_GREEN,
                        font=("Segoe UI", 12, "bold"),
                        spacing1=14, spacing3=4)
        t.tag_configure("name_system", foreground=_ORANGE,
                        font=("Segoe UI", 11, "bold"),
                        spacing1=10, spacing3=4)

        # Message bodies
        t.tag_configure("body_user", foreground=_FG, spacing1=1, spacing3=3)
        t.tag_configure("body_azmuth", foreground=_FG, spacing1=1, spacing3=3)
        t.tag_configure("body_system", foreground="#a6adc8",
                        spacing1=1, spacing3=3)
        t.tag_configure("body_error", foreground=_RED, spacing1=1, spacing3=3)

        # Meta line under each message
        t.tag_configure("meta", foreground=_DIM,
                        font=("Segoe UI", 9), spacing1=4, spacing3=16)

        # Inline markdown
        t.tag_configure("bold", font=("Segoe UI", 11, "bold"),
                        foreground=_YELLOW)
        t.tag_configure("italic", font=("Segoe UI", 11, "italic"))
        t.tag_configure("inline_code", font=("Consolas", 10),
                        background=_INLINE_BG, foreground=_PINK)

        # Headers
        t.tag_configure("h1", font=("Segoe UI", 16, "bold"),
                        foreground=_BLUE, spacing1=10, spacing3=6)
        t.tag_configure("h2", font=("Segoe UI", 14, "bold"),
                        foreground=_BLUE, spacing1=8, spacing3=4)
        t.tag_configure("h3", font=("Segoe UI", 12, "bold"),
                        foreground=_PURPLE, spacing1=6, spacing3=3)
        t.tag_configure("h4", font=("Segoe UI", 11, "bold"),
                        foreground=_PURPLE, spacing1=4, spacing3=2)

        # Lists and quotes
        t.tag_configure("bullet", lmargin1=22, lmargin2=36, spacing1=2)
        t.tag_configure("numbered", lmargin1=22, lmargin2=36, spacing1=2)
        t.tag_configure("quote", lmargin1=22, lmargin2=22,
                        foreground="#a6adc8",
                        font=("Segoe UI", 11, "italic"))

        # Code block body
        t.tag_configure("code_block", font=("Consolas", 10),
                        background=_CODE_BG, foreground=_FG,
                        lmargin1=20, lmargin2=20, spacing1=0, spacing3=0)

        # Code block header (clickable)
        t.tag_configure("code_header", font=("Segoe UI", 9),
                        foreground=_BLUE, background=_CODE_BG,
                        lmargin1=20, lmargin2=20, spacing1=6, spacing3=0)

    # -- render -----------------------------------------------------------
    def _render_all(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._last_rendered_id = 0
        self._block_counter = 0
        self._code_blocks.clear()
        self._text.configure(state="disabled")
        for entry in self._state.conversation:
            self._render_entry(entry)
            self._last_rendered_id = entry.id

    def _render_entry(self, entry) -> None:  # noqa: ANN001
        label = {Speaker.USER: "You",
                 Speaker.AZMUTH: "Azmuth",
                 Speaker.SYSTEM: "System"}[entry.speaker]
        name_tag = {Speaker.USER: "name_user",
                    Speaker.AZMUTH: "name_azmuth",
                    Speaker.SYSTEM: "name_system"}[entry.speaker]
        body_tag = {Speaker.USER: "body_user",
                    Speaker.AZMUTH: "body_azmuth",
                    Speaker.SYSTEM: "body_system"}[entry.speaker]
        if entry.level.value == "error":
            body_tag = "body_error"

        self._text.configure(state="normal")
        self._text.insert("end", f"{label}\n", (name_tag,))
        self._render_markdown(entry.text, body_tag)
        meta_parts = [entry.created_at.strftime("%H:%M:%S")]
        if entry.language:
            meta_parts.append(entry.language)
        meta_parts.append(entry.source)
        if entry.model:
            meta_parts.append(entry.model)
        if entry.provider:
            meta_parts.append(f"[{entry.provider}]")
        self._text.insert("end", "   ·   ".join(meta_parts) + "\n", ("meta",))
        self._text.configure(state="disabled")
        self._text.see("end")

    # -- markdown ---------------------------------------------------------
    def _render_markdown(self, text: str, base_tag: str) -> None:
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            m = _FENCE_OPEN.match(line)
            if m:
                lang = m.group(1) or "code"
                code_lines: list[str] = []
                i += 1
                while i < len(lines) and not lines[i].startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    i += 1
                self._render_code_block(lang, "\n".join(code_lines))
                continue

            m = _HEADER.match(line)
            if m:
                level = len(m.group(1))
                tag = {1: "h1", 2: "h2", 3: "h3", 4: "h4"}.get(level, "h4")
                self._insert_inline(m.group(2), base_tag, extra=tag)
                self._text.insert("end", "\n")
                i += 1
                continue

            m = _BULLET.match(line)
            if m:
                self._text.insert("end", "  •  ", (base_tag, "bullet"))
                self._insert_inline(m.group(2), base_tag, extra="bullet")
                self._text.insert("end", "\n")
                i += 1
                continue

            m = _NUMBERED.match(line)
            if m:
                self._text.insert(
                    "end", f"  {m.group(2)}.  ",
                    (base_tag, "numbered"),
                )
                self._insert_inline(m.group(3), base_tag, extra="numbered")
                self._text.insert("end", "\n")
                i += 1
                continue

            m = _QUOTE.match(line)
            if m:
                self._text.insert("end", "▎  ", (base_tag, "quote"))
                self._insert_inline(m.group(1), base_tag, extra="quote")
                self._text.insert("end", "\n")
                i += 1
                continue

            self._insert_inline(line, base_tag)
            self._text.insert("end", "\n")
            i += 1

    def _insert_inline(
        self, line: str, base_tag: str, extra: str | None = None
    ) -> None:
        for text, style in _tokenize_inline(line):
            tags: list[str] = [base_tag]
            if extra:
                tags.append(extra)
            if style:
                tags.append(style)
            self._text.insert("end", text, tuple(tags))

    # -- code blocks ------------------------------------------------------
    def _render_code_block(self, lang: str, code: str) -> None:
        self._block_counter += 1
        block_id = f"codeblock_{self._block_counter}"
        header_id = f"codeheader_{self._block_counter}"
        self._code_blocks[block_id] = code

        header_text = f"  📋  {lang}   ·   click to copy\n"
        self._text.insert("end", header_text, (header_id, "code_header"))
        self._text.tag_bind(
            header_id, "<Button-1>",
            lambda _e, b=block_id, h=header_id: self._copy_block(b, h),
        )
        self._text.tag_bind(
            header_id, "<Enter>",
            lambda _e: self._text.configure(cursor="hand2"),
        )
        self._text.tag_bind(
            header_id, "<Leave>",
            lambda _e: self._text.configure(cursor="xterm"),
        )

        for line in code.split("\n"):
            self._text.insert(
                "end", "  " + line + "\n", (block_id, "code_block")
            )
        self._text.insert("end", "\n", ("code_block",))

    def _copy_block(self, block_id: str, header_id: str) -> None:
        content = self._code_blocks.get(block_id, "")
        if not content:
            return
        try:
            self._text.clipboard_clear()
            self._text.clipboard_append(content)
        except tk.TclError:
            return
        # Flash the header to confirm.
        self._text.tag_configure(header_id, background=_CODE_HOVER)
        self._text.after(
            180,
            lambda: self._text.tag_configure(
                header_id, background=_CODE_BG
            ),
        )

    # -- state subscription ----------------------------------------------
    def _on_state_changed(self, state: UIState) -> None:
        new_entries = [
            e for e in state.conversation if e.id > self._last_rendered_id
        ]
        if not new_entries:
            return
        for entry in new_entries:
            self._render_entry(entry)
            self._last_rendered_id = entry.id


# --------------------------------------------------------------------------
# Inline tokenizer
# --------------------------------------------------------------------------
def _tokenize_inline(text: str) -> list[tuple[str, str | None]]:
    """Split a line into (text, style) segments.

    Style is None, "bold", "italic", or "inline_code". Innermost marker
    wins when markers nest.
    """
    return _tokenize(text, None)


def _tokenize(
    text: str, style: str | None
) -> list[tuple[str, str | None]]:
    if not text:
        return []
    matches: list[tuple[int, int, str, str]] = []
    for m in _BOLD.finditer(text):
        matches.append((m.start(), m.end(), "bold", m.group(1)))
    for m in _ITALIC.finditer(text):
        matches.append((m.start(), m.end(), "italic", m.group(1)))
    for m in _INLINE_CODE.finditer(text):
        matches.append((m.start(), m.end(), "inline_code", m.group(1)))

    if not matches:
        return [(text, style)]

    matches.sort(key=lambda x: x[0])
    start, end, inner_style, content = matches[0]

    out: list[tuple[str, str | None]] = []
    if start > 0:
        out.append((text[:start], style))
    out.extend(_tokenize(content, inner_style))
    out.extend(_tokenize(text[end:], style))
    return out

