"""Runtime context: current date, time, and (best-effort) location.

Everything here is a *snapshot* taken at the start of a session. Date
and time are read at prompt-build time so the model always sees "today"
accurately. Location comes from a manual override (settings) if set,
otherwise from IP geolocation cached for the process lifetime.

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

_CURRENCY_BY_COUNTRY: dict[str, str] = {
    "IN": "INR", "US": "USD", "GB": "GBP", "EU": "EUR",
    "JP": "JPY", "CN": "CNY", "AU": "AUD", "CA": "CAD",
    "SG": "SGD", "AE": "AED", "CH": "CHF",
}


@dataclass(frozen=True, slots=True)
class Location:
    city: str | None = None
    region: str | None = None
    country: str | None = None
    country_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    currency: str | None = None
    locale: str | None = None
    units: str = "metric"
    source: str = "unknown"

    @property
    def is_known(self) -> bool:
        return bool(self.city or self.region or self.country)

    def display(self) -> str:
        parts = [p for p in (self.city, self.region, self.country) if p]
        return ", ".join(parts) if parts else "unknown"

    def search_hint(self) -> str:
        """Short location string appended to regional search queries."""
        parts = [p for p in (self.city, self.region, self.country) if p]
        return " ".join(parts)


class LocationService:
    """Detects the user's approximate location.

    Precedence:
      1. ``manual`` Location passed to the constructor (from settings) — never
         hits the network and always wins.
      2. IP-based lookup via ipapi.co — cached for the process lifetime.
      3. ``None`` — the prompt simply omits the location line.
    """

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        manual: Location | None = None,
    ) -> None:
        self._timeout = timeout
        self._manual = manual
        self._location: Location | None = None
        self._attempted = False

    # -- public read ----------------------------------------------------
    @property
    def location(self) -> Location | None:
        """Return the effective location (manual if set, else cached IP)."""
        if self._manual is not None:
            return self._manual
        return self._location

    # -- detection ------------------------------------------------------
    def detect(self, *, force: bool = False) -> Location | None:
        """Return the cached location, or detect it once.

        ``force=True`` re-queries even if a detection was already
        attempted. Never raises.
        """
        # Manual override short-circuits everything.
        if self._manual is not None:
            return self._manual

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

        cc = (payload.get("country_code") or "").upper()
        location = Location(
            city=payload.get("city") or None,
            region=payload.get("region") or None,
            country=payload.get("country_name") or None,
            country_code=cc or None,
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
            timezone=payload.get("timezone") or None,
            currency=_CURRENCY_BY_COUNTRY.get(cc),
            locale=(payload.get("languages") or "").split(",")[0] or None,
            source="ip",
        )
        self._location = location
        logger.info(
            "location detected",
            extra={
                "event": "LOCATION_DETECTED",
                "city": location.city,
                "country": location.country,
            },
        )
        return location


def build_location_service(settings) -> LocationService:
    """Construct a LocationService from settings.

    If any ``AZMUTH_LOCATION_*`` setting is present, the location is
    pinned to those values and IP detection is never used.
    """
    manual: Location | None = None
    city = getattr(settings, "location_city", None)
    if city:
        manual = Location(
            city=city,
            region=getattr(settings, "location_region", None),
            country=getattr(settings, "location_country", None),
            country_code=getattr(settings, "location_country_code", None),
            timezone=getattr(settings, "location_timezone", None),
            currency=getattr(settings, "location_currency", None),
            units=getattr(settings, "location_units", "metric"),
            source="manual",
        )
    return LocationService(manual=manual)


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
        lines = [
            "## Current context",
            f"Today is {self.date_str}.",
            f"The current time is {self.time_str}.",
        ]

        loc = self.location
        if loc is not None and loc.is_known:
            lines.append(f"The user is located in {loc.display()}.")
            prefs: list[str] = []
            if loc.currency:
                prefs.append(f"currency: {loc.currency}")
            if loc.timezone:
                prefs.append(f"timezone: {loc.timezone}")
            if loc.units:
                prefs.append(f"units: {loc.units}")
            if prefs:
                lines.append(f"User preferences — {'; '.join(prefs)}.")
        else:
            lines.append("The user's location is unknown.")

        currency_hint = loc.currency if loc and loc.currency else "the user's local currency"

        lines.extend([
            "",
            "## Search enrichment rules",
            "When you call search_web for a topic that is regional, retail, "
            "price-related, news-related, or weather-related, ALWAYS append "
            "the user's city and country to the query — even if the user "
            "did not mention their location. Examples:",
            '  user: "what is today gold rate"',
            '  search query: "gold rate today Chennai Tamil Nadu India"',
            "",
            '  user: "weather tomorrow"',
            '  search query: "weather tomorrow Chennai Tamil Nadu India"',
            "",
            "For prices, prefer sources that quote the user's local currency "
            f"({currency_hint}) and local units. For gold in India, report in "
            "INR per gram and per sovereign (8g). For gold in the US, report "
            "in USD per troy ounce.",
            "",
            "When reporting any price, always state: (a) the source website, "
            "(b) the unit, (c) the currency, and (d) the timestamp the source "
            "gives. If sources disagree, say so and cite each.",
            "",
            "Treat the facts above as authoritative. If the user asks about "
            "the current date or time, use the values given — do not guess. "
            "If you are unsure whether your training data is current, say so "
            "and offer to search the web.",
        ])
        return "\n".join(lines)


__all__ = [
    "Location",
    "LocationService",
    "RuntimeContext",
    "build_location_service",
]