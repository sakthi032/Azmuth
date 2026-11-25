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


def _get_gemini_credentials() -> tuple[str | None, str, str]:
    """Return (api_key, base_url, model).

    Accepts GEMINI_API_KEY or GOOGLE_API_KEY.
    Ensures the base URL is properly parsed to standard Google Generative Language REST path.
    """
    import urllib.parse
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    base_raw = os.getenv("GEMINI_BASE_URL")
    if not api_key or not base_raw:
        try:
            from app.config.settings import load_settings
            s = load_settings()
            if not api_key:
                api_key = s.gemini_api_key
            if not base_raw:
                base_raw = s.gemini_base_url
        except Exception:
            pass

    if not api_key:
        return None, "https://generativelanguage.googleapis.com/v1beta", os.getenv("VISION_MODEL", "gemini-flash-lite-latest")

    if not isinstance(base_raw, str):
        base_raw = str(base_raw or "https://generativelanguage.googleapis.com/v1beta")
    parsed = urllib.parse.urlparse(base_raw)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or "generativelanguage.googleapis.com"
    base = f"{scheme}://{host}/v1beta"
    model = os.getenv("VISION_MODEL", "gemini-flash-lite-latest")
    return api_key, base, model


async def ground_target(
    png_bytes: bytes,
    query: str,
    *,
    timeout: float = 30.0,
) -> VlmTarget | None:
    """Ask the VLM to locate ``query`` in the screenshot."""
    api_key, base, model = _get_gemini_credentials()
    if not api_key:
        raise ProviderUnavailableError(
            "GEMINI_API_KEY not set — VLM tier unavailable"
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


async def describe_image(
    image_bytes: bytes,
    prompt: str,
    *,
    timeout: float = 60.0,
) -> str:
    """Ask the VLM a free-form question about an image.

    Returns the raw text answer, or "" if the call fails or the model
    produced nothing. Never raises; callers treat "" as unavailable.
    """
    api_key, base, model = _get_gemini_credentials()
    if not api_key:
        logger.warning(
            "GEMINI_API_KEY not set — describe_image unavailable",
            extra={"event": "VLM_DESCRIBE_NO_KEY"},
        )
        return ""

    url = f"{base}/models/{model}:generateContent?key={api_key}"

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/png", "data": b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 2048,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=payload)
            if r.status_code >= 400:
                logger.warning(
                    "vlm describe failed",
                    extra={
                        "event": "VLM_DESCRIBE_FAILED",
                        "status": r.status_code,
                    },
                )
                return ""
            data = r.json()
    except Exception:
        logger.exception("vlm describe request failed")
        return ""

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return ""


async def describe_image_detailed(
    image_bytes: bytes,
    prompt: str,
    *,
    timeout: float = 60.0,
    mime_type: str = "image/png",
) -> tuple[str, str]:
    """Like describe_image, but returns ``(answer, error_reason)``.

    ``error_reason`` is ``""`` on success. On failure it is a
    human-readable string the tool can surface directly: missing key,
    quota exceeded, unsupported format, too large, network error, etc.
    ``answer`` is ``""`` on failure.
    """
    api_key, base, model = _get_gemini_credentials()
    if not api_key:
        return "", "GEMINI_API_KEY is not set — vision unavailable"

    url = f"{base}/models/{model}:generateContent?key={api_key}"

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 2048,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=payload)
    except httpx.TimeoutException:
        return "", "vision API timed out — try a smaller region or retry"
    except httpx.RequestError as exc:
        return "", f"vision network error: {exc}"
    except Exception as exc:
        logger.exception("vlm describe request raised")
        return "", f"vision request failed: {exc}"

    if r.status_code in (401, 403):
        return "", (
            f"vision API auth failed (HTTP {r.status_code}) — check "
            f"GEMINI_API_KEY"
        )
    if r.status_code == 429:
        return "", "vision API quota exceeded — try again later"
    if r.status_code == 413:
        return "", "image too large for the vision API"
    if r.status_code >= 400:
        detail_msg = ""
        try:
            err_json = r.json()
            if isinstance(err_json, dict) and "error" in err_json:
                detail_msg = err_json["error"].get("message", "")
        except Exception:
            detail_msg = r.text[:200]
        if api_key and detail_msg:
            detail_msg = detail_msg.replace(api_key, "***")
        err_str = f"vision API HTTP {r.status_code}"
        if detail_msg:
            err_str += f": {detail_msg}"
        logger.warning(
            "vlm describe failed",
            extra={
                "event": "VLM_DESCRIBE_FAILED",
                "status": r.status_code,
                "reason": err_str,
            },
        )
        return "", err_str

    try:
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return str(text), ""
    except (KeyError, IndexError, TypeError, ValueError):
        return "", (
            "vision model returned no text — the image may be blank "
            "or the prompt was filtered"
        )


__all__ = [
    "VlmTarget", "ground_target", "describe_image",
    "describe_image_detailed",
]
