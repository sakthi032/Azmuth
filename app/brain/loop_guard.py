"""LoopGuard — detects and breaks infinite tool-call loops in the agent.

Ported from OpenJarvis architecture (reference only — written from scratch).
No external dependencies; uses only stdlib ``hashlib`` and ``collections``.

Design
------
* **Identical-call detection** — each tool call is hashed (tool name +
  sorted arguments).  If the same hash fires ≥ ``max_identical`` times
  consecutively the guard raises ``LoopDetectedError``.
* **Ping-pong detection** — a sliding window of the last
  ``pingpong_window`` hashes is checked for A-B-A-B patterns (any two
  alternating hashes that together fill the window).
* **Context-compression trigger** — when the conversation reaches
  ``context_max_messages`` the guard signals that the caller should
  compress the context (drop oldest non-system messages) before
  continuing.
* **Budget enforcement** — a hard cap on total tool calls per turn.

Usage::

    guard = LoopGuard()
    guard.check(tool_name="search_web", arguments={"query": "foo"})
    # raises LoopDetectedError if a loop is detected
    guard.maybe_compress(messages)  # compresses in-place if needed
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class LoopDetectedError(RuntimeError):
    """Raised when a repetitive tool-call pattern is detected."""

    def __init__(self, reason: str, call_hash: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.call_hash = call_hash


@dataclass
class LoopGuardConfig:
    """Tuneable parameters for :class:`LoopGuard`."""

    max_identical: int = 3
    """Maximum consecutive identical calls before blocking."""

    pingpong_window: int = 6
    """Sliding window size for A-B-A-B cycle detection (must be even ≥ 4)."""

    max_tool_budget: int = 50
    """Hard cap on total tool calls in one agent turn."""

    context_max_messages: int = 100
    """Trigger context compression when conversation exceeds this count."""

    context_keep_recent: int = 20
    """Number of recent messages to keep during compression."""

    warn_threshold: int = 2
    """Emit a warning (but not an error) when identical count reaches this."""


class LoopGuard:
    """Stateful loop-detection guard for one agent turn.

    Instantiate fresh per turn / per request so state does not leak.

    Parameters
    ----------
    config:
        Optional :class:`LoopGuardConfig`; uses defaults if not provided.
    """

    def __init__(self, config: LoopGuardConfig | None = None) -> None:
        self._cfg = config or LoopGuardConfig()
        self._total_calls: int = 0
        # Maps call-hash → consecutive occurrence count
        self._consecutive: dict[str, int] = {}
        self._last_hash: str | None = None
        # Sliding window for ping-pong detection
        self._window: deque[str] = deque(maxlen=self._cfg.pingpong_window)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Check a proposed tool call; raise :class:`LoopDetectedError` if
        it looks like part of a loop.

        Parameters
        ----------
        tool_name:
            The name of the tool being called.
        arguments:
            The arguments dict (will be JSON-serialised for hashing; order
            does not matter).

        Raises
        ------
        LoopDetectedError
            When an identical-call run or a ping-pong cycle is detected.
        RuntimeError
            When the total call budget is exhausted.
        """
        self._total_calls += 1
        if self._total_calls > self._cfg.max_tool_budget:
            raise LoopDetectedError(
                f"Tool budget exhausted after {self._cfg.max_tool_budget} calls.",
                call_hash="BUDGET_EXCEEDED",
            )

        h = self._hash(tool_name, arguments)
        self._check_identical(h, tool_name)
        self._window.append(h)
        self._check_pingpong(tool_name)
        self._last_hash = h

    def maybe_compress(self, messages: list[dict[str, Any]]) -> bool:
        """Compress ``messages`` in-place if the conversation is too long.

        Keeps the first (system) message and the ``context_keep_recent``
        most-recent messages.  Returns ``True`` if compression happened.

        Parameters
        ----------
        messages:
            The mutable list of chat messages (dicts with ``"role"`` and
            ``"content"`` keys).
        """
        if len(messages) <= self._cfg.context_max_messages:
            return False

        logger.warning(
            "LoopGuard: compressing context from %d → %d messages",
            len(messages),
            self._cfg.context_keep_recent + 1,
        )

        # Preserve system message(s) at the front
        system_msgs = [m for m in messages if m.get("role") == "system"]
        recent = [m for m in messages if m.get("role") != "system"][
            -self._cfg.context_keep_recent :
        ]
        messages.clear()
        messages.extend(system_msgs)
        messages.extend(recent)
        return True

    @property
    def total_calls(self) -> int:
        """Total number of tool calls checked so far in this turn."""
        return self._total_calls

    @property
    def remaining_budget(self) -> int:
        """Remaining tool calls before the hard budget is hit."""
        return max(0, self._cfg.max_tool_budget - self._total_calls)

    def reset(self) -> None:
        """Reset state (useful for reusing across sub-turns)."""
        self._total_calls = 0
        self._consecutive.clear()
        self._last_hash = None
        self._window.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(tool_name: str, arguments: dict[str, Any]) -> str:
        """SHA-256 of ``tool_name`` + canonically sorted argument JSON."""
        try:
            args_json = json.dumps(arguments, sort_keys=True, default=str)
        except (TypeError, ValueError):
            args_json = str(arguments)
        raw = f"{tool_name}:{args_json}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _check_identical(self, h: str, tool_name: str) -> None:
        """Track consecutive identical hashes and raise when limit hit."""
        if h == self._last_hash:
            self._consecutive[h] = self._consecutive.get(h, 1) + 1
        else:
            self._consecutive[h] = 1

        count = self._consecutive[h]

        if count >= self._cfg.warn_threshold:
            logger.warning(
                "LoopGuard: tool %r called identically %d time(s) in a row",
                tool_name,
                count,
            )

        if count >= self._cfg.max_identical:
            raise LoopDetectedError(
                f"Tool '{tool_name}' called identically {count} consecutive "
                f"time(s) — aborting to prevent infinite loop.",
                call_hash=h,
            )

    def _check_pingpong(self, tool_name: str) -> None:
        """Detect A-B-A-B ping-pong in the sliding window."""
        win = list(self._window)
        size = len(win)
        required = self._cfg.pingpong_window

        if size < required:
            return  # window not full yet

        # Check if the window is entirely composed of two alternating hashes
        a, b = win[0], win[1]
        if a == b:
            return  # not A-B; will be caught by identical check

        expected = [(a if i % 2 == 0 else b) for i in range(required)]
        if win == expected:
            raise LoopDetectedError(
                f"Ping-pong loop detected for tool '{tool_name}': "
                f"A-B pattern repeated {required // 2} times.",
                call_hash=f"{a[:8]}↔{b[:8]}",
            )
