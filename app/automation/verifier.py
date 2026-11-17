"""Snapshot-diff verification for click and type actions.

The problem: click_visual returns coordinates and stops. It has no idea
whether the click actually did anything. The button might have moved,
the app might be busy, another window might have stolen focus.

The fix: take a lightweight signature of the target window before the
action, take it again after, compare. If nothing meaningful changed and
the model expected change, retry once with a re-resolve.

The signature is a sorted list of (name, control_type, rect) tuples for
every interactive element in the window. It is deliberately coarse —
we're looking for "did the UI respond", not pixel-diffing the screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config.logging_setup import get_logger
from app.automation.uia import UIAutomationController

logger = get_logger("automation.verifier")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A coarse fingerprint of a window's interactive state."""
    window_title: str
    element_count: int
    signature: frozenset[tuple[str, str, tuple[int, int, int, int]]]
    # A few sample element names, kept for diagnostics.
    sample: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "window": self.window_title,
            "element_count": self.element_count,
            "sample": list(self.sample[:8]),
        }


class Verifier:
    """Take, compare, and interpret UI snapshots."""

    def __init__(self, uia: UIAutomationController) -> None:
        self._uia = uia

    async def snapshot(self, window: str | int | None) -> Snapshot | None:
        """Capture a signature of the given window.

        ``window=None`` uses the currently-focused window. Returns None
        if the window cannot be read at all (which usually means the
        caller should not attempt verification).
        """
        try:
            tree = await self._uia.get_window_tree(
                window=window, interactive_only=True, limit=200
            )
        except Exception as exc:
            logger.info(
                "snapshot failed",
                extra={"event": "VERIFIER_SNAPSHOT_FAILED", "reason": str(exc)},
            )
            return None

        elements = tree.get("elements") or []
        sig: set[tuple[str, str, tuple[int, int, int, int]]] = set()
        names: list[str] = []
        for e in elements:
            name = (e.get("name") or "").strip()
            ctype = e.get("control_type") or ""
            rect = e.get("rect") or [0, 0, 0, 0]
            try:
                rect_t = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
            except Exception:
                rect_t = (0, 0, 0, 0)
            sig.add((name, ctype, rect_t))
            if name:
                names.append(name)

        return Snapshot(
            window_title=str(tree.get("window", "")),
            element_count=len(elements),
            signature=frozenset(sig),
            sample=tuple(names[:20]),
        )

    @staticmethod
    def changed(before: Snapshot | None, after: Snapshot | None) -> bool:
        """Return True if the two snapshots differ meaningfully.

        Rules:
        - If either is None, we can't tell → treat as unchanged.
        - Element count differing by >= 1 is a change.
        - Any element added or removed from the signature is a change.
        - Pure position shifts of the same set of elements is NOT a change
          (scroll, hover, animation).
        """
        if before is None or after is None:
            return False
        if abs(before.element_count - after.element_count) >= 1:
            return True

        before_names = {(n, t) for n, t, _ in before.signature}
        after_names = {(n, t) for n, t, _ in after.signature}
        return before_names != after_names

    @staticmethod
    def describe_change(before: Snapshot | None, after: Snapshot | None) -> str:
        """Human-readable summary of what changed. For logs and retries."""
        if before is None or after is None:
            return "no snapshot available"
        added = {n for n, _ in [(x[0], x[1]) for x in after.signature]} - \
                {n for n, _ in [(x[0], x[1]) for x in before.signature]}
        removed = {n for n, _ in [(x[0], x[1]) for x in before.signature]} - \
                  {n for n, _ in [(x[0], x[1]) for x in after.signature]}
        parts = []
        if added:
            parts.append(f"appeared: {sorted(added)[:3]}")
        if removed:
            parts.append(f"disappeared: {sorted(removed)[:3]}")
        if not parts:
            parts.append(
                f"count {before.element_count} → {after.element_count}"
            )
        return "; ".join(parts)


__all__ = ["Snapshot", "Verifier"]