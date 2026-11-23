"""Image-to-text via the vision provider, with realistic error reporting."""

from __future__ import annotations

from pathlib import Path

from app.automation.vision import describe_image_detailed
from app.config.logging_setup import get_logger
from app.documents.extractors import ExtractedBlock, ExtractedDocument

logger = get_logger("documents.image")

_PROMPT = (
    "Describe this image in full detail. If it contains text, transcribe "
    "every word verbatim, preserving layout. If it contains a table, "
    "reproduce it row by row. If it is a photo, describe what is visible "
    "and any legible text. If it is a chart, describe axes, values, and "
    "trends. The reader cannot see the image — be their eyes."
)

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}


def _mime_for(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "image/jpeg")


def _prepare_image_bytes(raw: bytes, path: Path) -> tuple[bytes, str]:
    """EXIF-rotate, RGB-convert, downscale to 2048 px, re-encode as JPEG.

    Returns (bytes, mime). On any Pillow error, returns the original
    bytes with the inferred MIME.
    """
    try:
        from PIL import Image, ImageOps
        import io
    except ImportError:
        return raw, _mime_for(path)

    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img) or img
        img = img.convert("RGB")

        max_side = 2048
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize(
                (int(w * scale), int(h * scale)), Image.LANCZOS
            )

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        logger.exception(
            "image prep failed; sending original bytes",
            extra={"event": "IMAGE_PREP_FAILED", "path": str(path)},
        )
        return raw, _mime_for(path)


async def extract_image(path: Path) -> ExtractedDocument:
    """Describe an image via the vision provider.

    Raises RuntimeError with a specific reason on failure — the tool
    layer forwards that reason to the user verbatim.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"could not read {path}: {exc}") from exc

    image_bytes, mime = _prepare_image_bytes(raw, path)
    logger.info(
        "image prepared for vision",
        extra={
            "event": "IMAGE_PREPARED",
            "path": str(path),
            "raw_bytes": len(raw),
            "prepared_bytes": len(image_bytes),
            "mime": mime,
        },
    )

    text = ""
    err = ""
    try:
        text, err = await describe_image_detailed(
            image_bytes, _PROMPT, mime_type=mime,
        )
    except Exception as exc:
        logger.exception("describe_image_detailed raised")
        err = f"vision request failed: {exc}"

    if err:
        logger.warning(
            "vision failed for image",
            extra={
                "event": "IMAGE_VISION_FAILED",
                "path": str(path),
                "reason": err,
            },
        )
        raise RuntimeError(err)

    if not text or not text.strip():
        raise RuntimeError(
            "vision model returned empty text — image may be blank "
            "or the prompt was filtered"
        )

    return ExtractedDocument(
        path=path,
        mime=mime,
        blocks=[ExtractedBlock(1, "Image", text)],
    )