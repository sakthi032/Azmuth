"""Three-tier target resolution.

Tier 1 — UIA        (fast, exact, ~5-30 ms)     — named Windows controls
Tier 2 — CV / OCR   (medium, ~200-400 ms)       — visible text labels
Tier 3 — VLM        (slow, ~800-1500 ms, net)   — visual descriptions

Every tier returns a Resolution. The caller decides whether to click,
verify, retry, or give up.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.automation.dpi import query_spaces
from app.automation.input import InputController
from app.automation.screen import CaptureResult, capture_full_screen, capture_window
from app.automation.uia import ElementInfo, UIAutomationController
from app.config.logging_setup import get_logger
from app.core.errors import AutomationError

logger = get_logger("automation.resolver")


class ResolutionTier(str, Enum):
    UIA = "uia"
    CV = "cv"
    VLM = "vlm"


@dataclass(frozen=True)
class Resolution:
    tier: ResolutionTier
    click_x: int          # in INPUT space — safe to pass to move_mouse/click
    click_y: int
    label: str
    confidence: float
    uia_element: ElementInfo | None = None
    reasoning: str = ""
    screenshot_xy: tuple[int, int] | None = None   # original, for debugging

    def as_dict(self) -> dict:
        d = {
            "tier": self.tier.value,
            "click": [self.click_x, self.click_y],
            "label": self.label,
            "confidence": round(self.confidence, 3),
        }
        if self.reasoning:
            d["reasoning"] = self.reasoning
        if self.screenshot_xy:
            d["screenshot_xy"] = list(self.screenshot_xy)
        return d


class TargetResolver:
    def __init__(
        self,
        *,
        uia: UIAutomationController,
        input_ctl: InputController,
        enable_cv: bool = True,
        enable_vlm: bool = True,
    ) -> None:
        self._uia = uia
        self._input = input_ctl
        self._enable_cv = enable_cv
        self._enable_vlm = enable_vlm

    async def resolve(
        self,
        query: str,
        *,
        window: str | int | None = None,
        allow_vlm: bool = True,
    ) -> Resolution:
        # ---- Tier 1: UIA ----
        try:
            elements = await self._uia.find_elements(
                name=query, limit=5, window=window
            )
            if elements:
                best = elements[0]
                if best.bounding_rect != (0, 0, 0, 0):
                    spaces = query_spaces()
                    cx_scr = (best.bounding_rect[0] + best.bounding_rect[2]) // 2
                    cy_scr = (best.bounding_rect[1] + best.bounding_rect[3]) // 2
                    cx_in, cy_in = spaces.screenshot_to_input(cx_scr, cy_scr)
                    # UIA rects are already in input space, so the two
                    # divisions above cancel out. Compute directly instead:
                    cx_in = cx_scr
                    cy_in = cy_scr
                    logger.info(
                        "resolved via UIA",
                        extra={
                            "event": "RESOLVER_UIA_HIT",
                            "query": query,
                            "label": best.name,
                            "score": best.score,
                        },
                    )
                    return Resolution(
                        tier=ResolutionTier.UIA,
                        click_x=cx_in,
                        click_y=cy_in,
                        label=best.name,
                        confidence=min(best.score, 1.0),
                        uia_element=best,
                    )
        except AutomationError:
            raise
        except Exception as exc:
            logger.info(
                "uia tier miss",
                extra={"event": "RESOLVER_UIA_MISS", "reason": repr(exc)},
            )

        # ---- Tier 2: CV ----
        if self._enable_cv:
            try:
                capture = await self._capture(window)
                from app.automation.cv import find_text_in_image
                matches = await find_text_in_image(capture.png_bytes, query)
                if matches:
                    m = matches[0]
                    spaces = query_spaces(screenshot_size=(capture.width, capture.height))
                    sx, sy = m.center
                    px, py = spaces.screenshot_to_input(sx, sy)
                    logger.info(
                        "resolved via CV",
                        extra={
                            "event": "RESOLVER_CV_HIT",
                            "query": query,
                            "text": m.text,
                            "screenshot_center": [sx, sy],
                            "input_center": [px, py],
                        },
                    )
                    return Resolution(
                        tier=ResolutionTier.CV,
                        click_x=px,
                        click_y=py,
                        label=m.text,
                        confidence=m.confidence,
                        reasoning=f"OCR matched {m.text!r} at screenshot ({sx},{sy})",
                        screenshot_xy=(sx, sy),
                    )
            except AutomationError:
                raise
            except Exception as exc:
                logger.info(
                    "cv tier miss",
                    extra={"event": "RESOLVER_CV_MISS", "reason": repr(exc)},
                )

        # ---- Tier 3: VLM ----
        if self._enable_vlm and allow_vlm:
            try:
                capture = await self._capture(window)
                from app.automation.vision import ground_target
                vt = await ground_target(capture.png_bytes, query)
                if vt is not None and vt.confidence >= 0.7:
                    spaces = query_spaces(screenshot_size=(capture.width, capture.height))
                    sx, sy = vt.center
                    px, py = spaces.screenshot_to_input(sx, sy)
                    logger.info(
                        "resolved via VLM",
                        extra={
                            "event": "RESOLVER_VLM_HIT",
                            "query": query,
                            "label": vt.label,
                            "confidence": vt.confidence,
                        },
                    )
                    return Resolution(
                        tier=ResolutionTier.VLM,
                        click_x=px,
                        click_y=py,
                        label=vt.label,
                        confidence=vt.confidence,
                        reasoning=vt.reasoning,
                        screenshot_xy=(sx, sy),
                    )
            except AutomationError:
                raise
            except Exception as exc:
                logger.info(
                    "vlm tier miss",
                    extra={"event": "RESOLVER_VLM_MISS", "reason": repr(exc)},
                )

        raise AutomationError(
            f"could not resolve target {query!r} via UIA, CV, or VLM"
        )

    async def _capture(self, window: str | int | None) -> CaptureResult:
        if window is None:
            return await capture_full_screen()

        if isinstance(window, int):
            return await capture_window(window)

        # Resolve a title substring to an HWND, then capture just that window.
        from app.automation.windows_mgmt import list_windows
        needle = window.strip().lower()
        for w in list_windows():
            if needle in (w.title or "").lower():
                return await capture_window(w.handle)

        logger.warning(
            "no window matched for capture, using full screen",
            extra={"event": "RESOLVER_WINDOW_MISS", "window": window},
        )
        return await capture_full_screen()


__all__ = ["Resolution", "ResolutionTier", "TargetResolver"]