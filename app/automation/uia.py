
"""Windows UI Automation - find and manipulate UI elements by name.

Wraps the ``uiautomation`` package. This is the layer that lets the AI
click "the Night light toggle" instead of clicking at (482, 361), which
breaks when the user has a different DPI, theme, or Windows build.

Element matching uses a word-overlap score rather than exact substring,
because Windows Settings renders labels with dynamic state suffixes
("Night light (off until 06:00 PM)") while the underlying UI element is
named just "Night light". Exact substring matching fails on that; word
overlap handles it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError, ProviderUnavailableError

logger = get_logger("automation.uia")

#: Words that carry no matching signal - they appear in every Settings
#: element name and would produce false matches.
_STOPWORDS = frozenset({
    "a", "an", "the", "to", "for", "of", "in", "on", "at", "and", "or",
    "is", "are", "was", "be",
})

_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {
        w.lower()
        for w in _WORD.findall(text)
        if w.lower() not in _STOPWORDS
    }


def _score(search: str, target: str) -> float:
    """Word-overlap score in [0, 1] between two element names.

    * 1.0  - exact case-insensitive match
    * high - all search tokens present in the target
    * low  - partial overlap
    * 0.0  - no meaningful overlap
    """
    if not search or not target:
        return 0.0
    if search.strip().lower() == target.strip().lower():
        return 1.0
    s_tokens = _tokens(search)
    t_tokens = _tokens(target)
    if not s_tokens:
        return 0.0
    overlap = s_tokens & t_tokens
    return len(overlap) / len(s_tokens)


@dataclass(frozen=True, slots=True)
class ElementInfo:
    name: str
    control_type: str
    automation_id: str
    class_name: str
    is_offscreen: bool
    is_enabled: bool
    score: float = 0.0
    # -- additions -----------------------------------------------------
    bounding_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    window_title: str = ""
    window_handle: int = 0
    depth: int = 0
    value: str = ""

    @property
    def is_interactive(self) -> bool:
        """True for control types the model can act on."""
        t = self.control_type.lower()
        return any(k in t for k in (
            "button", "edit", "combobox", "listitem", "menuitem",
            "tabitem", "checkbox", "radiobutton", "hyperlink",
            "treeitem", "slider", "spinner", "splitbutton",
        ))

    def as_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            "control_type": self.control_type,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "is_offscreen": self.is_offscreen,
            "is_enabled": self.is_enabled,
            "score": round(self.score, 2),
        }
        if self.bounding_rect != (0, 0, 0, 0):
            d["rect"] = list(self.bounding_rect)
        if self.window_title:
            d["window"] = self.window_title
        if self.value:
            d["value"] = self.value
        return d


def _load_uia():
    try:
        import uiautomation  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ProviderUnavailableError(
            "UI Automation requires the 'uiautomation' package. "
            "Install it with: pip install uiautomation"
        ) from exc
    return uiautomation

import threading

_com_initialized = threading.local()


from functools import wraps

def _requires_com(fn):
    """Ensure COM is initialized on whatever thread runs this function."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _ensure_com_initialized()
        return fn(*args, **kwargs)
    return wrapper

def _ensure_com_initialized() -> None:
    """Initialize COM on the current thread, exactly once.

    UI Automation is a COM API. COM must be initialized per thread,
    with the correct apartment model. ``asyncio.to_thread`` uses
    arbitrary threads from a pool, and those threads have no COM.

    We initialize as STA (single-threaded apartment), which is what
    desktop UI Automation clients use. The initialization is cached in
    thread-local storage so we don't call it repeatedly.
    """
    if getattr(_com_initialized, "done", False):
        return
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    COINIT_APARTMENTTHREADED = 0x2
    # RPC_E_CHANGED_MODE means COM was already initialized with a
    # different apartment model. That is fine — we can still call UIA.
    RPC_E_CHANGED_MODE = 0x80010106
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    if hr not in (0, RPC_E_CHANGED_MODE):
        raise RuntimeError(f"CoInitializeEx failed: 0x{hr & 0xFFFFFFFF:08X}")
    _com_initialized.done = True
    

class UIAutomationController:
    """Find and control UI elements on the desktop."""

    def __init__(
        self, *, max_depth: int = 10, min_score: float = 0.5
    ) -> None:
        self._max_depth = max_depth
        self._min_score = min_score

    def is_available(self) -> bool:
        try:
            _load_uia()
            return True
        except ProviderUnavailableError:
            return False
    # -- window-scoped discovery ------------------------------------------
    async def get_window_tree(
        self,
        *,
        window: str | None = None,
        interactive_only: bool = True,
        max_depth: int = 8,
        limit: int = 120,
    ) -> dict[str, Any]:
        """Return the UIA tree of a window (or the focused one).

        ``window=None`` uses the foreground window. ``window="notepad"``
        matches the first top-level window whose title contains that text.
        Only elements that are enabled and on-screen are returned.
        """
        import asyncio

        @_requires_com
        def _tree() -> dict[str, Any]:
            uia = _load_uia()
            _ensure_com_initialized()
            uia = _load_uia()

            if window:
                root, title, handle = self._find_top_window(uia, window)
                if root is None:
                    visible = self._visible_window_titles(uia)
                    raise AutomationError(
                        f"no window matched {window!r}. "
                        f"Visible top-level windows: {visible}"
                    )
            else:
                root = uia.GetForegroundControl()
                if root is None:
                    raise AutomationError("no foreground window")
                try:
                    title = (root.Name or "").strip()
                    handle = int(getattr(root, "NativeWindowHandle", 0) or 0)
                except Exception:
                    title, handle = "", 0

            elements: list[ElementInfo] = []
            try:
                for ctrl, depth in uia.WalkControl(root, maxDepth=max_depth):
                    if depth == 0:
                        continue
                    if len(elements) >= limit:
                        break
                    try:
                        info = self._control_to_info(
                            ctrl, depth=depth, title=title, handle=handle
                        )
                    except Exception:
                        continue
                    if info is None:
                        continue
                    if info.is_offscreen or not info.is_enabled:
                        continue
                    if interactive_only and not info.is_interactive:
                        continue
                    elements.append(info)
            except Exception:  # noqa: BLE001
                pass

            return {
                "window": title,
                "handle": handle,
                "count": len(elements),
                "elements": [e.as_dict() for e in elements],
            }

        return await asyncio.to_thread(_tree)

    async def read_focused_document(self) -> str | None:
        """Read the text of the focused document control.

        Returns None if no TextPattern is available — in which case the
        caller must verify some other way. Does NOT raise on failure;
        this is a best-effort read.
        """
        import asyncio

        @_requires_com
        def _read() -> str | None:
            uia = _load_uia()
            root = uia.GetForegroundControl()
            if root is None:
                return None
            try:
                for ctrl, _depth in uia.WalkControl(root, maxDepth=6):
                    try:
                        ctype = (ctrl.ControlTypeName or "").lower()
                    except Exception:
                        continue
                    if "document" not in ctype and "edit" not in ctype:
                        continue
                    if not getattr(ctrl, "IsTextPatternAvailable", False):
                        continue
                    try:
                        pattern = ctrl.GetTextPattern()
                        rng = pattern.DocumentRange
                        text = rng.GetText(-1) or ""
                        return text
                    except Exception:
                        continue
            except Exception:
                return None
            return None

        return await asyncio.to_thread(_read)

    # -- internal helpers --------------------------------------------------
    @staticmethod
    def _find_top_window(uia, title_substring: str):
        root = uia.GetRootControl()
        needle = title_substring.strip().lower()
        try:
            children = root.GetChildren()
        except Exception:
            return None, "", 0
        for child in children:
            try:
                name = (child.Name or "").strip()
            except Exception:
                continue
            if needle in name.lower():
                handle = int(getattr(child, "NativeWindowHandle", 0) or 0)
                return child, name, handle
        return None, "", 0

    @staticmethod
    def _visible_window_titles(uia, limit: int = 12) -> list[str]:
        root = uia.GetRootControl()
        out: list[str] = []
        try:
            for child in root.GetChildren():
                try:
                    name = (child.Name or "").strip()
                except Exception:
                    continue
                if not name:
                    continue
                if name not in out:
                    out.append(name)
                if len(out) >= limit:
                    break
        except Exception:
            pass
        return out

    @staticmethod
    def _control_to_info(
        ctrl, *, depth: int, title: str, handle: int
    ) -> ElementInfo | None:
        try:
            name = (ctrl.Name or "").strip()
            ctype = ctrl.ControlTypeName or ""
            cid = ctrl.AutomationId or ""
            cclass = ctrl.ClassName or ""
            offscreen = bool(getattr(ctrl, "IsOffscreen", False))
            enabled = bool(getattr(ctrl, "IsEnabled", True))
        except Exception:
            return None

        # Synthesize a name for unnamed edit-type controls so the model
        # can find them. Win11 Notepad's RichEditD2DPT has no Name.
        if not name:
            ctype_lower = ctype.lower()
            if "edit" in ctype_lower or "document" in ctype_lower:
                name = f"<{ctype or 'Edit'}>"
            elif "richtext" in cclass.lower():
                name = "<RichText editor>"
            else:
                return None  # truly nameless non-edit control — skip

        rect = (0, 0, 0, 0)
        try:
            r = ctrl.BoundingRectangle
            if r and r.right > r.left and r.bottom > r.top:
                rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            pass

        value = ""
        try:
            vp = getattr(ctrl, "GetValuePattern", None) 
            if vp and getattr(ctrl, "IsValuePatternAvailable", False):
                value = str(vp().Value or "")[:200]
        except Exception:
            pass

        return ElementInfo(
            name=name,
            control_type=ctype,
            automation_id=cid,
            class_name=cclass,
            is_offscreen=offscreen,
            is_enabled=enabled,
            bounding_rect=rect,
            window_title=title,
            window_handle=handle,
            depth=depth,
            value=value,
        )
    # -- discovery ---------------------------------------------------------
    async def find_elements(
        self,
        *,
        name: str | None = None,
        control_type: str | None = None,
        automation_id: str | None = None,
        limit: int = 20,
        window: str | int | None = None,
    ) -> list[ElementInfo]:
        """Find UI elements.

        ``window`` scopes the search: pass a title substring or an HWND
        to search only that window's subtree. If ``None``, searches the
        whole desktop — which is what allows a similarly-named element
        in another app to match. Always pass ``window`` when you know it.
        """
        import asyncio

        @_requires_com
        def _find() -> list[ElementInfo]:
            uia = _load_uia()

            if window is not None:
                root = self._resolve_window_root(uia, window)
                if root is None:
                    raise AutomationError(
                        f"no window matched {window!r}; "
                        f"cannot scope element search"
                    )
            else:
                root = uia.GetRootControl()

            found: list[ElementInfo] = []
            # Walk the root's CHILDREN, not the root itself — if the root
            # is a window, we don't want to match its own title.
            try:
                children = root.GetChildren()
            except Exception:
                children = [root]
            for child in children:
                self._walk(
                    child, found,
                    name=name, control_type=control_type,
                    automation_id=automation_id,
                    limit=limit * 4, depth=0,
                )
            found.sort(key=lambda e: (-e.score, e.name))
            return found[:limit]

        return await asyncio.to_thread(_find)

    @staticmethod
    def _resolve_window_root(uia, window: str | int):
        """Return the UIA control for a window matched by title or HWND."""
        root = uia.GetRootControl()
        try:
            children = root.GetChildren()
        except Exception:
            return None

        # HWND match
        if isinstance(window, int):
            for child in children:
                try:
                    h = int(getattr(child, "NativeWindowHandle", 0) or 0)
                except Exception:
                    continue
                if h == window:
                    return child
            return None

        # Title substring match
        needle = window.strip().lower()
        for child in children:
            try:
                t = (child.Name or "").strip().lower()
            except Exception:
                continue
            if needle and needle in t:
                return child
        return None

    def _walk(
        self,
        control: Any,
        out: list[ElementInfo],
        *,
        name: str | None,
        control_type: str | None,
        automation_id: str | None,
        limit: int,
        depth: int,
    ) -> None:
        if len(out) >= limit or depth > self._max_depth:
            return
        try:
            children = control.GetChildren()
        except Exception:  # noqa: BLE001
            return

        for child in children:
            if len(out) >= limit:
                return
            try:
                cname = (child.Name or "").strip()
                ctype = child.ControlTypeName or ""
                cid = child.AutomationId or ""
                cclass = child.ClassName or ""
            except Exception:  # noqa: BLE001
                continue

            score = 1.0
            if name is not None:
                score = _score(name, cname)
                if score < self._min_score:
                    score = 0.0
            if control_type is not None and control_type.lower() not in ctype.lower():
                score = 0.0
            if automation_id is not None and automation_id != cid:
                score = 0.0

            if score > 0.0:
                out.append(
                    ElementInfo(
                        name=cname,
                        control_type=ctype,
                        automation_id=cid,
                        class_name=cclass,
                        is_offscreen=bool(getattr(child, "IsOffscreen", False)),
                        is_enabled=bool(getattr(child, "IsEnabled", True)),
                        score=score,
                    )
                )

            self._walk(
                child, out,
                name=name, control_type=control_type,
                automation_id=automation_id, limit=limit, depth=depth + 1,
            )

    # -- interaction -------------------------------------------------------
    async def invoke_element(
        self,
        *,
        name: str,
        control_type: str | None = None,
    ) -> bool:
        import asyncio

        @_requires_com
        def _invoke() -> bool:
            uia = _load_uia()
            root = uia.GetRootControl()

            best = None
            best_score = 0.0
            for ctrl, _depth in uia.WalkControl(root, maxDepth=self._max_depth):
                try:
                    cname = (ctrl.Name or "").strip()
                    ctype = ctrl.ControlTypeName or ""
                except Exception:  # noqa: BLE001
                    continue
                if not cname or getattr(ctrl, "IsOffscreen", False):
                    continue
                if control_type is not None and control_type.lower() not in ctype.lower():
                    continue
                score = _score(name, cname)
                if score > best_score:
                    best_score = score
                    best = ctrl
                    if score >= 0.99:
                        break

            if best is None or best_score < self._min_score:
                raise AutomationError(
                    f"no UI element matching {name!r} was found "
                    f"(best score {best_score:.2f}). "
                    f"Try list_ui_elements with a shorter name to see "
                    f"what is on screen."
                )

            # Try native patterns first.
            for pattern_getter, action in [
                ("GetInvokePattern", lambda p: p.Invoke()),
                ("GetTogglePattern", lambda p: p.Toggle()),
                ("GetSelectionItemPattern", lambda p: p.Select()),
            ]:
                try:
                    available = getattr(best, f"Is{pattern_getter[3:]}Available", False)
                    if available and hasattr(best, pattern_getter):
                        getattr(best, pattern_getter)().Invoke() if False else None
                        pattern = getattr(best, pattern_getter)()
                        action(pattern)
                        return True
                except Exception:  # noqa: BLE001
                    continue

            # Fall back to a physical click at the element's centre.
            # Use SendInput via InputController — mouse_event is deprecated
            # and doesn't give the target window's child controls keyboard
            # focus on Windows 10/11.
            rect = best.BoundingRectangle
            cx = int((rect.left + rect.right) / 2)
            cy = int((rect.top + rect.bottom) / 2)
            try:
                import asyncio as _asyncio
                from app.automation.input import InputController
                ctl = InputController()
                _asyncio.run(ctl.click(cx, cy))
            except Exception as exc:
                raise AutomationError(
                    f"element matched but could not be clicked: {exc}"
                ) from exc
            return True

        invoked = await asyncio.to_thread(_invoke)
        logger.info(
            "ui element invoked",
            extra={"event": "UIA_ELEMENT_INVOKED", "name": name},
        )
        return invoked

    async def set_toggle(self, *, name: str, on: bool) -> bool:
        import asyncio

        @_requires_com
        def _set() -> bool:
            uia = _load_uia()
            root = uia.GetRootControl()

            best = None
            best_score = 0.0
            for ctrl, _depth in uia.WalkControl(root, maxDepth=self._max_depth):
                try:
                    cname = (ctrl.Name or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                if not cname or getattr(ctrl, "IsOffscreen", False):
                    continue
                if not getattr(ctrl, "IsTogglePatternAvailable", False):
                    continue
                score = _score(name, cname)
                if score > best_score:
                    best_score = score
                    best = ctrl
                    if score >= 0.99:
                        break

            if best is None or best_score < self._min_score:
                # Return the closest toggle names so the AI can retry
                # once with the right spelling instead of guessing.
                nearby: list[str] = []
                for ctrl, _ in uia.WalkControl(root, maxDepth=self._max_depth):
                    try:
                        cname = (ctrl.Name or "").strip()
                    except Exception:  # noqa: BLE001
                        continue
                    if not cname or getattr(ctrl, "IsOffscreen", False):
                        continue
                    if not getattr(ctrl, "IsTogglePatternAvailable", False):
                        continue
                    if cname not in nearby:
                        nearby.append(cname)
                    if len(nearby) >= 8:
                        break
                hint = (
                    " Visible toggles on screen: "
                    + ", ".join(repr(n) for n in nearby)
                    if nearby
                    else " No toggles are visible. Call open_settings_page first."
                )
                raise AutomationError(
                    f"no toggle matching {name!r} was found "
                    f"(best score {best_score:.2f}).{hint}"
                )

            pattern = best.GetTogglePattern()
            current = pattern.ToggleState  # 0=off, 1=on, 2=indeterminate
            want_on = 1 if on else 0
            if current == want_on:
                return True
            pattern.Toggle()
            return True

        result = await asyncio.to_thread(_set)
        logger.info(
            "toggle set",
            extra={"event": "UIA_TOGGLE_SET", "name": name, "on": on},
        )
        return result


__all__ = ["ElementInfo", "UIAutomationController"]
