"""The conversation view with markdown rendering and typewriter streaming.

Renders the transcript with:

* speaker names in bold, color-coded per role
* markdown: headers, bold, italic, inline code, lists, block quotes
* fenced code blocks with a click-to-copy header bar
* a dim meta line (time . language . source) under each message
* an animated thinking indicator when awaiting a response
* a welcome message when the conversation is empty
* character-by-character streaming for Azmuth replies
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from app.ui.state import Speaker, UIState

# ── Palette ──────────────────────────────────────────────────────────
_BG      = "#1e1e2e"
_FG      = "#cdd6f4"
_DIM     = "#6c7086"
_BLUE    = "#89b4fa"
_GREEN   = "#a6e3a1"
_ORANGE  = "#fab387"
_RED     = "#f38ba8"
_PURPLE  = "#cba6f7"
_PINK    = "#f5c2e7"
_YELLOW  = "#f9e2af"
_MANTLE  = "#181825"
_SURF0   = "#313244"
_SURF1   = "#45475a"
_OVL1    = "#7f849c"
_SUB0    = "#a6adc8"

# ── Markdown patterns ────────────────────────────────────────────────
_FENCE_OPEN  = re.compile(r"^```(\w*)\s*$")
_HEADER      = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_BULLET      = re.compile(r"^(\s*)[-*+]\s+(.+)$")
_NUMBERED    = re.compile(r"^(\s*)(\d+)\.\s+(.+)$")
_QUOTE       = re.compile(r"^>\s?(.*)$")
_BOLD        = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC      = re.compile(r"(?<!\w)\*(\S(?:[^*\n]*?\S)?)\*(?!\w)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")

# ── Timing (milliseconds) ───────────────────────────────────────────
_THINK_INTERVAL = 600
# Typewriter delays — adaptive, slows at start, accelerates later
_STREAM_DELAY_SLOW   = 20   # first ~80 chars
_STREAM_DELAY_MEDIUM = 12   # chars 80-300
_STREAM_DELAY_FAST   = 6    # chars 300+
_STREAM_THRESHOLD_1  = 80
_STREAM_THRESHOLD_2  = 300
_STREAM_DELAY_ATOMIC = 4    # markdown tokens / code lines
_STREAM_DELAY_NEWLINE = 22


class TranscriptView(tk.Frame):
    """Scrollable conversation transcript with typewriter streaming."""

    def __init__(self, parent: tk.Widget, state: UIState) -> None:
        super().__init__(parent, bg=_BG)
        self._state = state
        self._last_rendered_id = 0
        self._block_counter = 0
        self._code_blocks: dict[str, str] = {}

        # Thinking indicator state
        self._thinking_active = False
        self._thinking_after_id: str | None = None
        self._thinking_step = 0

        # Streaming state
        self._streaming = False
        self._stream_after_id: str | None = None
        self._stream_units: list = []
        self._stream_index = 0
        self._stream_chars_done = 0
        self._stream_entry = None
        self._stream_name_start: str = "1.0"

        self._welcome_active = True

        self._text = tk.Text(
            self, wrap="word", state="disabled",
            background=_BG, foreground=_FG,
            insertbackground=_FG, selectbackground=_SURF1,
            borderwidth=0, highlightthickness=0,
            font=("Segoe UI", 11),
            padx=18, pady=14, cursor="xterm",
        )
        scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=scrollbar.set)

        self._text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._configure_tags()
        self._render_welcome()
        self._unsubscribe = state.subscribe(self._on_state_changed)

    def destroy(self) -> None:
        try:
            self._unsubscribe()
        except Exception:
            pass
        self._cancel_thinking_timer()
        self._cancel_stream_timer()
        super().destroy()

    # ── Tags ───────────────────────────────────────────────────────────
    def _configure_tags(self) -> None:
        t = self._text

        t.tag_configure("name_user", foreground=_BLUE,
                        font=("Segoe UI", 12, "bold"),
                        spacing1=16, spacing3=4)
        t.tag_configure("name_azmuth", foreground=_GREEN,
                        font=("Segoe UI", 12, "bold"),
                        spacing1=16, spacing3=4)
        t.tag_configure("name_system", foreground=_ORANGE,
                        font=("Segoe UI", 11, "bold"),
                        spacing1=10, spacing3=4)

        t.tag_configure("body_user", foreground=_FG, spacing1=1, spacing3=3)
        t.tag_configure("body_azmuth", foreground=_FG, spacing1=1, spacing3=3)
        t.tag_configure("body_system", foreground=_SUB0, spacing1=1, spacing3=3)
        t.tag_configure("body_error", foreground=_RED, spacing1=1, spacing3=3)

        t.tag_configure("meta", foreground=_DIM,
                        font=("Segoe UI", 9), spacing1=4, spacing3=18)

        t.tag_configure("bold", font=("Segoe UI", 11, "bold"),
                        foreground=_YELLOW)
        t.tag_configure("italic", font=("Segoe UI", 11, "italic"))
        t.tag_configure("inline_code", font=("Consolas", 10),
                        background=_SURF0, foreground=_PINK)

        t.tag_configure("h1", font=("Segoe UI", 16, "bold"),
                        foreground=_BLUE, spacing1=12, spacing3=6)
        t.tag_configure("h2", font=("Segoe UI", 14, "bold"),
                        foreground=_BLUE, spacing1=10, spacing3=4)
        t.tag_configure("h3", font=("Segoe UI", 12, "bold"),
                        foreground=_PURPLE, spacing1=8, spacing3=3)
        t.tag_configure("h4", font=("Segoe UI", 11, "bold"),
                        foreground=_PURPLE, spacing1=6, spacing3=2)

        t.tag_configure("bullet", lmargin1=24, lmargin2=38, spacing1=2)
        t.tag_configure("numbered", lmargin1=24, lmargin2=38, spacing1=2)
        t.tag_configure("quote", lmargin1=24, lmargin2=24,
                        foreground=_SUB0, font=("Segoe UI", 11, "italic"))

        t.tag_configure("code_block", font=("Consolas", 10),
                        background=_MANTLE, foreground=_FG,
                        lmargin1=22, lmargin2=22,
                        spacing1=0, spacing3=0)
        t.tag_configure("code_header", font=("Segoe UI", 9),
                        foreground=_BLUE, background=_MANTLE,
                        lmargin1=22, lmargin2=22,
                        spacing1=6, spacing3=0)

        t.tag_configure("thinking_name", foreground=_GREEN,
                        font=("Segoe UI", 12, "bold"),
                        spacing1=16, spacing3=4)
        t.tag_configure("thinking_body", foreground=_OVL1,
                        font=("Segoe UI", 11, "italic"),
                        spacing1=1, spacing3=3)

        t.tag_configure("welcome_title", foreground=_FG,
                        font=("Segoe UI", 20, "bold"),
                        spacing1=80, spacing3=8, justify="center")
        t.tag_configure("welcome_sub", foreground=_OVL1,
                        font=("Segoe UI", 11),
                        spacing3=30, justify="center")
        t.tag_configure("welcome_hint", foreground=_DIM,
                        font=("Segoe UI", 10, "italic"),
                        spacing1=20, justify="center")

    # ── Welcome screen ─────────────────────────────────────────────────
    def _render_welcome(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("end", "\n  Azmuth\n", ("welcome_title",))
        self._text.insert("end",
            "  Your personal AI agent \u2014 ready to help.\n",
            ("welcome_sub",))
        self._text.insert("end",
            "  Type a message below to get started.\n",
            ("welcome_hint",))
        self._text.configure(state="disabled")
        self._welcome_active = True

    def _clear_welcome(self) -> None:
        if not self._welcome_active:
            return
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")
        self._welcome_active = False

    # ── Normal (instant) render ────────────────────────────────────────
    def _render_all(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._last_rendered_id = 0
        self._block_counter = 0
        self._code_blocks.clear()
        self._text.configure(state="disabled")
        self._welcome_active = False
        for entry in self._state.conversation:
            self._render_entry(entry)
            self._last_rendered_id = entry.id

    def _render_entry(self, entry) -> None:
        self._clear_welcome()

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
        self._render_meta(entry)
        self._text.configure(state="disabled")
        self._text.see("end")

    def _render_meta(self, entry) -> None:
        meta_parts = [entry.created_at.strftime("%H:%M:%S")]
        if entry.language:
            meta_parts.append(entry.language)
        meta_parts.append(entry.source)
        if entry.model:
            meta_parts.append(entry.model)
        if entry.provider:
            meta_parts.append(f"[{entry.provider}]")
        self._text.insert("end",
            "   \u00b7   ".join(meta_parts) + "\n", ("meta",))

    # ── Typewriter streaming ───────────────────────────────────────────
    def _render_entry_streamed(self, entry) -> None:
        """Render an AZMUTH entry with character-by-character streaming."""
        self._clear_welcome()

        # Render speaker name immediately
        self._text.configure(state="normal")
        self._text.insert("end", "Azmuth\n", ("name_azmuth",))
        name_start = self._text.index("end - 1c linestart")
        self._text.configure(state="disabled")

        # Build the stream units from markdown-parsed text
        units = self._build_stream_units(entry.text, "body_azmuth")

        if not units:
            # Empty body — just add meta and done
            self._text.configure(state="normal")
            self._render_meta(entry)
            self._text.configure(state="disabled")
            self._text.see("end")
            return

        # Start streaming
        self._streaming = True
        self._stream_units = units
        self._stream_index = 0
        self._stream_chars_done = 0
        self._stream_entry = entry
        self._stream_name_start = name_start
        self._tick_stream()

    def _build_stream_units(
        self, text: str, base_tag: str
    ) -> list[tuple[str, tuple[str, ...], int]]:
        """Parse text into (text, tags, delay_hint) units for streaming.

        delay_hint is a category: 0 = regular char, 1 = atomic token,
        2 = newline, 3 = structural (bullet etc.)
        """
        units: list[tuple[str, tuple[str, ...], int]] = []
        lines = text.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]

            # ── Code fence ──
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
                # Code header — atomic
                units.append((
                    f"  \u2398  {lang}   \u00b7   click to copy\n",
                    ("code_header",), 1))
                # Code body — each line atomic
                for cl in code_lines:
                    units.append(("  " + cl + "\n", ("code_block",), 1))
                units.append(("\n", ("code_block",), 2))
                continue

            # ── Header ──
            m = _HEADER.match(line)
            if m:
                level = len(m.group(1))
                tag = {1: "h1", 2: "h2", 3: "h3", 4: "h4"}.get(level, "h4")
                for ch in m.group(2):
                    units.append((ch, (base_tag, tag), 0))
                units.append(("\n", (base_tag,), 2))
                i += 1
                continue

            # ── Bullet ──
            m = _BULLET.match(line)
            if m:
                units.append(("  \u2022  ", (base_tag, "bullet"), 3))
                self._add_inline_units(m.group(2), base_tag, "bullet", units)
                units.append(("\n", (base_tag,), 2))
                i += 1
                continue

            # ── Numbered ──
            m = _NUMBERED.match(line)
            if m:
                units.append((f"  {m.group(2)}.  ",
                              (base_tag, "numbered"), 3))
                self._add_inline_units(m.group(3), base_tag, "numbered", units)
                units.append(("\n", (base_tag,), 2))
                i += 1
                continue

            # ── Quote ──
            m = _QUOTE.match(line)
            if m:
                units.append(("\u258e  ", (base_tag, "quote"), 3))
                self._add_inline_units(m.group(1), base_tag, "quote", units)
                units.append(("\n", (base_tag,), 2))
                i += 1
                continue

            # ── Regular line ──
            self._add_inline_units(line, base_tag, None, units)
            units.append(("\n", (base_tag,), 2))
            i += 1

        return units

    def _add_inline_units(
        self,
        line: str,
        base_tag: str,
        extra: str | None,
        units: list,
    ) -> None:
        """Tokenize inline markdown and append stream units."""
        for text, style in _tokenize_inline(line):
            tags = [base_tag]
            if extra:
                tags.append(extra)
            if style:
                tags.append(style)

            if style in ("bold", "italic", "inline_code"):
                # Markdown token — appear atomically
                units.append((text, tuple(tags), 1))
            else:
                # Regular text — character by character
                for ch in text:
                    units.append((ch, tuple(tags), 0))

    def _tick_stream(self) -> None:
        """Process one stream unit per tick for typewriter effect."""
        if not self._streaming:
            return

        if self._stream_index >= len(self._stream_units):
            self._finish_stream()
            return

        text, tags, hint = self._stream_units[self._stream_index]

        # Insert this unit
        self._text.configure(state="normal")
        self._text.insert("end", text, tags)
        self._text.configure(state="disabled")
        self._text.see("end")

        self._stream_index += 1
        self._stream_chars_done += len(text)

        # Calculate adaptive delay
        delay = self._stream_delay(hint)
        self._stream_after_id = self._text.after(delay, self._tick_stream)

    def _stream_delay(self, hint: int) -> int:
        """Return ms delay for the next tick, adaptive to position."""
        if hint == 1:       # atomic markdown token
            return _STREAM_DELAY_ATOMIC
        if hint == 2:       # newline
            return _STREAM_DELAY_NEWLINE
        if hint == 3:       # structural (bullet etc.)
            return 2

        # Regular character — adaptive speed
        done = self._stream_chars_done
        if done < _STREAM_THRESHOLD_1:
            return _STREAM_DELAY_SLOW
        if done < _STREAM_THRESHOLD_2:
            return _STREAM_DELAY_MEDIUM
        return _STREAM_DELAY_FAST

    def _finish_stream(self) -> None:
        """Streaming complete: re-render with full markdown + code bindings."""
        self._streaming = False
        self._cancel_stream_timer()
        entry = self._stream_entry
        if entry is None:
            return

        # Delete the streamed text (from name line to end)
        self._text.configure(state="normal")
        self._text.delete(self._stream_name_start, "end - 1c")
        self._text.configure(state="disabled")

        # Re-render the entry with full markdown + code bindings + meta
        self._render_entry(entry)

    def _finish_stream_immediately(self) -> None:
        """Cancel streaming and render the full entry right now."""
        if not self._streaming:
            return
        self._streaming = False
        self._cancel_stream_timer()
        entry = self._stream_entry
        if entry is None:
            return
        # Delete streamed text
        self._text.configure(state="normal")
        self._text.delete(self._stream_name_start, "end - 1c")
        self._text.configure(state="disabled")
        # Full render
        self._render_entry(entry)

    def _cancel_stream_timer(self) -> None:
        if self._stream_after_id is not None:
            try:
                self._text.after_cancel(self._stream_after_id)
            except Exception:
                pass
            self._stream_after_id = None

    # ── Thinking indicator ─────────────────────────────────────────────
    def _cancel_thinking_timer(self) -> None:
        if self._thinking_after_id is not None:
            try:
                self._text.after_cancel(self._thinking_after_id)
            except Exception:
                pass
            self._thinking_after_id = None

    def _show_thinking(self) -> None:
        if self._thinking_active:
            return
        self._thinking_active = True
        self._thinking_step = 0
        self._text.configure(state="normal")
        self._text.insert("end", "Azmuth\n", ("thinking_name",))
        self._text.insert("end", "  Thinking ...\n", ("thinking_body",))
        self._text.configure(state="disabled")
        self._text.see("end")
        self._tick_thinking()

    def _tick_thinking(self) -> None:
        if not self._thinking_active:
            return
        self._thinking_step = (self._thinking_step + 1) % 4
        dots = "." * self._thinking_step
        pad = " " * (3 - self._thinking_step)
        display = f"  Thinking{dots}{pad}\n"

        ranges = self._text.tag_ranges("thinking_body")
        if ranges and len(ranges) >= 2:
            self._text.configure(state="normal")
            self._text.delete(ranges[0], ranges[1])
            self._text.insert(ranges[0], display, ("thinking_body",))
            self._text.configure(state="disabled")

        self._thinking_after_id = self._text.after(
            _THINK_INTERVAL, self._tick_thinking)

    def _hide_thinking(self) -> None:
        if not self._thinking_active:
            return
        self._thinking_active = False
        self._cancel_thinking_timer()

        name_ranges = self._text.tag_ranges("thinking_name")
        body_ranges = self._text.tag_ranges("thinking_body")

        if (name_ranges and body_ranges
                and len(name_ranges) >= 2 and len(body_ranges) >= 2):
            self._text.configure(state="normal")
            self._text.delete(name_ranges[0], body_ranges[1])
            self._text.configure(state="disabled")

    # ── Markdown ───────────────────────────────────────────────────────
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
                self._text.insert("end", "  \u2022  ", (base_tag, "bullet"))
                self._insert_inline(m.group(2), base_tag, extra="bullet")
                self._text.insert("end", "\n")
                i += 1
                continue

            m = _NUMBERED.match(line)
            if m:
                self._text.insert("end", f"  {m.group(2)}.  ",
                                  (base_tag, "numbered"))
                self._insert_inline(m.group(3), base_tag, extra="numbered")
                self._text.insert("end", "\n")
                i += 1
                continue

            m = _QUOTE.match(line)
            if m:
                self._text.insert("end", "\u258e  ", (base_tag, "quote"))
                self._insert_inline(m.group(1), base_tag, extra="quote")
                self._text.insert("end", "\n")
                i += 1
                continue

            self._insert_inline(line, base_tag)
            self._text.insert("end", "\n")
            i += 1

    def _insert_inline(self, line: str, base_tag: str,
                       extra: str | None = None) -> None:
        for text, style in _tokenize_inline(line):
            tags: list[str] = [base_tag]
            if extra:
                tags.append(extra)
            if style:
                tags.append(style)
            self._text.insert("end", text, tuple(tags))

    # ── Code blocks ────────────────────────────────────────────────────
    def _render_code_block(self, lang: str, code: str) -> None:
        self._block_counter += 1
        block_id = f"codeblock_{self._block_counter}"
        header_id = f"codeheader_{self._block_counter}"
        self._code_blocks[block_id] = code

        header_text = f"  \u2398  {lang}   \u00b7   click to copy\n"
        self._text.insert("end", header_text, (header_id, "code_header"))
        self._text.tag_bind(
            header_id, "<Button-1>",
            lambda _e, b=block_id, h=header_id: self._copy_block(b, h))
        self._text.tag_bind(
            header_id, "<Enter>",
            lambda _e: self._text.configure(cursor="hand2"))
        self._text.tag_bind(
            header_id, "<Leave>",
            lambda _e: self._text.configure(cursor="xterm"))

        for line in code.split("\n"):
            self._text.insert("end", "  " + line + "\n",
                              (block_id, "code_block"))
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
        self._text.tag_configure(header_id, background=_SURF1)
        self._text.after(
            180,
            lambda: self._text.tag_configure(
                header_id, background=_MANTLE))

    # ── State subscription ─────────────────────────────────────────────
    def _on_state_changed(self, state: UIState) -> None:
        new_entries = [
            e for e in state.conversation if e.id > self._last_rendered_id
        ]
        if not new_entries:
            return

        # If currently streaming, finish immediately before new entries
        if self._streaming:
            self._finish_stream_immediately()

        # Hide thinking before any AZMUTH messages
        for entry in new_entries:
            if entry.speaker == Speaker.AZMUTH:
                self._hide_thinking()
                break

        # Find the last AZMUTH entry — that one gets streamed
        last_azmuth_idx = -1
        for idx, entry in enumerate(new_entries):
            if entry.speaker == Speaker.AZMUTH:
                last_azmuth_idx = idx

        # Render all entries
        for idx, entry in enumerate(new_entries):
            should_stream = (
                entry.speaker == Speaker.AZMUTH
                and idx == last_azmuth_idx
            )
            if should_stream:
                self._render_entry_streamed(entry)
            else:
                self._render_entry(entry)
            self._last_rendered_id = entry.id

        # Show thinking if the last new entry is from the user
        if new_entries[-1].speaker == Speaker.USER:
            self._show_thinking()


# ── Inline tokenizer ────────────────────────────────────────────────
def _tokenize_inline(text: str) -> list[tuple[str, str | None]]:
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