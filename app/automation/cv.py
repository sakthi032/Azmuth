"""Computer vision primitives for visual target detection.

Two capabilities, in order of reliability:

1. OCR text search — find a label like "Save" or "Download". Workhorse
   for text-labeled buttons. Uses PaddleOCR which handles UI text well.

2. Template matching — find an icon by comparing against a stored
   reference image. For icon-only buttons with no readable text.

Everything returns coordinates in screenshot space. Callers MUST pass
those through ``app.automation.dpi.CoordinateSpaces.screenshot_to_input``
before handing them to InputController. Skipping that step puts every
click at the wrong location under DPI scaling.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.logging_setup import get_logger
from app.core.errors import ProviderUnavailableError

logger = get_logger("automation.cv")


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class VisualMatch:
    text: str
    x: int          # top-left, screenshot coords
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "confidence": round(self.confidence, 3),
            "center": list(self.center),
        }


# --------------------------------------------------------------------------
# PaddleOCR — lazily instantiated, cached for the process lifetime
# --------------------------------------------------------------------------
_ocr_instance: Any = None
_ocr_init_attempted = False


def _get_ocr():
    """Return a PaddleOCR instance, or None if unavailable.

    Uses the minimal constructor that works across PaddleOCR 2.x and 3.x.
    Extra kwargs like show_log / use_angle_cls are version-specific and
    cause TypeError on the wrong major version.
    """
    global _ocr_instance, _ocr_init_attempted
    if _ocr_init_attempted:
        return _ocr_instance
    _ocr_init_attempted = True

    try:
        from paddleocr import PaddleOCR
    except ImportError:
        logger.warning(
            "paddleocr not installed — CV tier disabled",
            extra={"event": "OCR_UNAVAILABLE"},
        )
        return None

    try:
        _ocr_instance = PaddleOCR(lang="en")
        logger.info("paddleocr initialized", extra={"event": "OCR_READY"})
    except Exception as exc:
        logger.warning(
            "paddleocr init failed",
            extra={"event": "OCR_INIT_FAILED", "reason": repr(exc)},
        )
        _ocr_instance = None

    return _ocr_instance


def _normalize_ocr_results(raw: Any) -> list[tuple[Any, str, float]]:
    """Normalize PaddleOCR output across versions to (bbox, text, score).

    bbox is a list of 4 [x,y] points (top-left, top-right, bottom-right,
    bottom-left) — the standard PaddleOCR format.
    """
    items: list[tuple[Any, str, float]] = []
    if not raw:
        return items

    first = raw[0]

    # -- PaddleOCR 3.x dict-style output --------------------------------
    if isinstance(first, dict):
        for entry in raw:
            texts = entry.get("rec_texts") or []
            scores = entry.get("rec_scores") or []
            polys = (
                entry.get("rec_polys")
                or entry.get("dt_polys")
                or entry.get("rec_boxes")
                or []
            )
            for bbox, text, conf in zip(polys, texts, scores):
                items.append((bbox, str(text), float(conf)))
        return items

    # -- PaddleOCR 3.x OCRResult object -------------------------------
    if hasattr(first, "get") and hasattr(first, "keys"):
        # It's a mapping-like object.
        for entry in raw:
            try:
                texts = entry.get("rec_texts") or []
                scores = entry.get("rec_scores") or []
                polys = entry.get("rec_polys") or entry.get("dt_polys") or []
                for bbox, text, conf in zip(polys, texts, scores):
                    items.append((bbox, str(text), float(conf)))
            except Exception:
                continue
        return items

    # -- PaddleOCR 2.x nested-list output ------------------------------
    # Structure: [ [ [bbox, (text, conf)], [bbox, (text, conf)], ... ] ]
    if isinstance(first, list):
        page = first
        for line in page:
            try:
                bbox = line[0]
                text_conf = line[1]
                text, conf = text_conf[0], text_conf[1]
                items.append((bbox, str(text), float(conf)))
            except Exception:
                continue
        return items

    return items


def _bbox_to_xywh(bbox: Any) -> tuple[int, int, int, int]:
    """Convert a PaddleOCR bbox to (x, y, w, h)."""
    # Standard: [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
    try:
        xs = [int(p[0]) for p in bbox]
        ys = [int(p[1]) for p in bbox]
        return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    except Exception:
        # Sometimes bbox is already (x, y, w, h).
        try:
            x, y, w, h = bbox[:4]
            return (int(x), int(y), int(w), int(h))
        except Exception:
            return (0, 0, 0, 0)


# --------------------------------------------------------------------------
# Public: OCR text search
# --------------------------------------------------------------------------
async def find_text_in_image(
    png_bytes: bytes,
    needle: str = "",
    *,
    min_confidence: float = 0.55,
    limit: int = 10,
) -> list[VisualMatch]:
    """Return all OCR regions containing ``needle`` (case-insensitive).

    Empty ``needle`` returns all text regions — useful for diagnostics
    when you do not know what is on screen.
    """
    def _do() -> list[VisualMatch]:
        import numpy as np
        from PIL import Image

        ocr = _get_ocr()
        if ocr is None:
            return []

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        arr = np.array(img)

        # --- Run OCR. Handle both APIs. ---
        raw: Any = None
        try:
            # 3.x
            if hasattr(ocr, "predict"):
                raw = ocr.predict(input=arr)
            # 2.x
            elif hasattr(ocr, "ocr"):
                raw = ocr.ocr(arr, cls=False)
        except TypeError:
            # Some 3.x builds use positional args only
            try:
                raw = ocr.predict(arr)
            except Exception as exc:
                logger.warning(
                    "ocr predict failed",
                    extra={"event": "OCR_PREDICT_FAILED", "reason": str(exc)},
                )
                return []
        except Exception as exc:
            logger.warning(
                "ocr call failed",
                extra={"event": "OCR_CALL_FAILED", "reason": str(exc)},
            )
            return []

        items = _normalize_ocr_results(raw)
        needle_lower = needle.strip().lower()

        matches: list[VisualMatch] = []
        for bbox, text, conf in items:
            if conf < min_confidence:
                continue
            if needle_lower and needle_lower not in text.lower():
                continue
            x, y, w, h = _bbox_to_xywh(bbox)
            if w <= 0 or h <= 0:
                continue
            matches.append(
                VisualMatch(
                    text=text,
                    x=x, y=y, width=w, height=h,
                    confidence=conf,
                )
            )

        # Prefer matches whose text length is closest to the needle —
        # avoids matching "Save as" when the user asked for "Save".
        if needle_lower:
            matches.sort(key=lambda m: (len(m.text), -m.confidence))
        else:
            matches.sort(key=lambda m: (-m.confidence, m.y, m.x))

        return matches[:limit]

    return await asyncio.to_thread(_do)


# --------------------------------------------------------------------------
# Public: template matching (icon-only buttons)
# --------------------------------------------------------------------------
async def find_template_in_image(
    png_bytes: bytes,
    template_path: Path | str,
    *,
    min_score: float = 0.85,
    limit: int = 5,
) -> list[VisualMatch]:
    """Find a template image in a screenshot via OpenCV matchTemplate.

    Returns matches above ``min_score``, sorted by confidence, with
    overlapping regions deduplicated. Coordinates are screenshot pixels.
    """
    template_path = Path(template_path)

    def _do() -> list[VisualMatch]:
        import cv2
        import numpy as np

        img_arr = np.frombuffer(png_bytes, dtype=np.uint8)
        haystack = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
        needle = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
        if haystack is None or needle is None:
            return []

        result = cv2.matchTemplate(haystack, needle, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= min_score)
        th, tw = needle.shape[:2]

        raw_matches: list[VisualMatch] = []
        for x, y in zip(xs.tolist(), ys.tolist()):
            raw_matches.append(
                VisualMatch(
                    text=f"<template:{template_path.name}>",
                    x=int(x), y=int(y),
                    width=int(tw), height=int(th),
                    confidence=float(result[y, x]),
                )
            )
        raw_matches.sort(key=lambda m: -m.confidence)

        # Deduplicate overlapping hits (same icon matched in a cluster).
        kept: list[VisualMatch] = []
        for m in raw_matches:
            if any(
                abs(m.x - k.x) < m.width // 2 and abs(m.y - k.y) < m.height // 2
                for k in kept
            ):
                continue
            kept.append(m)
            if len(kept) >= limit:
                break
        return kept

    return await asyncio.to_thread(_do)


# --------------------------------------------------------------------------
# Public: describe what is on screen (diagnostic)
# --------------------------------------------------------------------------
async def list_all_text(png_bytes: bytes, *, min_confidence: float = 0.55) -> list[VisualMatch]:
    """Return every text region OCR can find. Diagnostic helper."""
    return await find_text_in_image(png_bytes, "", min_confidence=min_confidence, limit=200)


__all__ = [
    "VisualMatch",
    "find_text_in_image",
    "find_template_in_image",
    "list_all_text",
]