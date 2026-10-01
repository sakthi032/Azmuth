
"""Runtime context: current date, time, and (best-effort) location.

Everything here is a *snapshot* taken at the start of a session. Date
and time are read at prompt-build time so the model always sees "today"
accurately. Location is detected once via IP geolocation and cached.

The location service is deliberately best-effort. If the network is
down or the geolocation service is unreachable, detection returns
``None`` and the prompt simply omits the location line. Nothing blocks.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config.logging_setup import get_logger

logger = get_logger("brain.context")


@dataclass(frozen=True, slots=True)
class Location:
    city: str | None = None
    region: str | None = None
    country: str | None = None
    country_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def is_known(self) -> bool:
        return bool(self.city or self.region or self.country)

    def display(self) -> str:
        parts = [p for p in (self.city, self.region, self.country) if p]
        return ", ".join(parts) if parts else "unknown"


class LocationService:
    """Detects the user's approximate location from their IP address.

    Uses https://ipapi.co/json/ — a free, no-key, OpenAI-adjacent
    service that returns city-level accuracy. The result is cached for
    the process lifetime.

    This is best-effort: a failure leaves ``location`` as ``None`` and
    the app runs without a location line in the prompt.
    """

    def __init__(self, *, timeout: float = 5.0) -> None:
        self._timeout = timeout
        self._location: Location | None = None
        self._attempted = False

    @property
    def location(self) -> Location | None:
        return self._location

    def detect(self, *, force: bool = False) -> Location | None:
        """Return the cached location, or detect it once.

        ``force=True`` re-queries even if a detection was already
        attempted. Never raises.
        """
        if self._attempted and not force:
            return self._location

        self._attempted = True
        try:
            with urllib.request.urlopen(
                "https://ipapi.co/json/", timeout=self._timeout
            ) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.info(
                "location detection failed",
                extra={"event": "LOCATION_DETECTION_FAILED", "reason": str(exc)},
            )
            self._location = None
            return None

        location = Location(
            city=payload.get("city") or None,
            region=payload.get("region") or None,
            country=payload.get("country_name") or None,
            country_code=payload.get("country_code") or None,
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
        )
        self._location = location
        logger.info(
            "location detected",
            extra={"event": "LOCATION_DETECTED", "city": location.city, "country": location.country},
        )
        return location


# --------------------------------------------------------------------------
# Per-turn context
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """A snapshot of the runtime facts the prompt needs.

    Built fresh for each turn so the time is always current.
    """

    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    location: Location | None = None

    @property
    def date_str(self) -> str:
        return self.now.strftime("%Y-%m-%d (%A)")

    @property
    def time_str(self) -> str:
        return self.now.strftime("%H:%M UTC")

    def as_prompt_block(self) -> str:
        """Render as a plain-text block for the system prompt."""
        lines = [
            "## Current context",
            f"Today is {self.date_str}.",
            f"The current time is {self.time_str}.",
        ]
        if self.location is not None and self.location.is_known:
            lines.append(f"The user is located in {self.location.display()}.")
        else:
            lines.append("The user's location is unknown.")
        lines.append(
            "Treat these facts as authoritative. If the user asks about "
            "the current date or time, use the values above — do not guess. "
            "If you are unsure whether your training data is current, say so "
            "and offer to search the web."
        )
        return "\n".join(lines)


__all__ = ["Location", "LocationService", "RuntimeContext"]
