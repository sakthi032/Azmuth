"""Vision-language model client.

Sends a screenshot + query to a VLM and asks for the pixel coordinates
of a target. Uses Gemini 2.5 Flash because it's cheap, fast, and the
vision API is stable.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass

import httpx

from app.config.logging_setup import get_logger
from app.core.errors import ProviderUnavailableError

logger = get_logger("automation.vision")


@dataclass(frozen=True)
class VlmTarget:
    x: int
    y: int
    width: int
    height: int
    label: str
    confidence: float
    reasoning: str

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def as_dict(self) -> dict:
        return {
            "x": self.x, "y": self.y,
            "width": self.width, "height": self.height,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "center": self.center,
            "reasoning": self.reasoning,
        }


_SYSTEM_PROMPT = """You are a UI grounding assistant. Given a screenshot
and a natural-language query, find the UI element the user is describing.

Return ONLY a JSON object with this exact shape:
{
  "found": true|false,
  "x": <int>, "y": <int>, "width": <int>, "height": <int>,
  "label": "<short text or description>",
  "confidence": 0.0-1.0,
  "reasoning": "<one sentence>"
}

Coordinates are in the image's own pixel space, origin top-left.
If nothing matches, return {"found": false, "reasoning": "..."}.
Never guess — if you cannot identify the target with confidence >= 0.7,
return found: false."""


async def ground_target(
    png_bytes: bytes,
    query: str,
    *,
    timeout: float = 30.0,
) -> VlmTarget | None:
    """Ask the VLM to locate ``query`` in the screenshot."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ProviderUnavailableError(
            "GEMINI_API_KEY not set — VLM tier unavailable"
        )

    model = os.getenv("VISION_MODEL", "gemini-2.5-flash")
    base = os.getenv(
        "GEMINI_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    )
    url = f"{base}/models/{model}:generateContent?key={api_key}"

    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "contents": [{
            "parts": [
                {"text": f"Find this UI element: {query}"},
                {"inline_data": {"mime_type": "image/png", "data": b64}},
            ]
        }],
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "maxOutputTokens": 512,
        },
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload)
        if r.status_code >= 400:
            logger.warning(
                "vlm call failed",
                extra={"event": "VLM_FAILED", "status": r.status_code},
            )
            return None
        data = r.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except Exception as exc:
        logger.warning(
            "vlm parse failed",
            extra={"event": "VLM_PARSE_FAILED", "reason": str(exc)},
        )
        return None

    if not parsed.get("found"):
        return None
    try:
        return VlmTarget(
            x=int(parsed["x"]),
            y=int(parsed["y"]),
            width=int(parsed.get("width", 1)),
            height=int(parsed.get("height", 1)),
            label=str(parsed.get("label", query)),
            confidence=float(parsed.get("confidence", 0.7)),
            reasoning=str(parsed.get("reasoning", "")),
        )
    except Exception:
        return None


__all__ = ["VlmTarget", "ground_target"]