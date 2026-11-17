"""Live weather tool via wttr.in — keyless, returns natural descriptions."""

from __future__ import annotations

import time
from typing import Any, ClassVar

import httpx

from app.config.logging_setup import get_logger
from app.core.errors import ProviderUnavailableError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool

logger = get_logger("tools.weather")

_CACHE_TTL = 600  # 10 minutes
_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_WTTR = "https://wttr.in/{city}?format=j1"


class GetWeatherTool(Tool):
    """Get current weather + short forecast for a city."""

    name: ClassVar[str] = "get_weather"
    description: ClassVar[str] = (
        "Get the current weather and a 3-day forecast for a city. "
        "USE THIS for any question about weather, temperature, rain, "
        "humidity, or forecast — including Tamil questions like "
        "'innaki mala varuma' or 'naalaiki varuma'. Far more accurate "
        "and faster than search_web for weather. "
        "Do NOT search the web for weather — call this tool."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": (
                    "City name in English. Omit to use the user's "
                    "current location from settings (Chennai)."
                ),
            },
        },
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, default_city: str = "Chennai") -> None:
        self._default_city = default_city

    def is_available(self) -> bool:
        return True

    async def execute(self, *, city: str | None = None) -> dict[str, Any]:
        target = (city or self._default_city).strip() or self._default_city
        data = await _fetch_weather(target)
        if data is None:
            raise ProviderUnavailableError(
                f"could not fetch weather for {target}"
            )
        return data


async def _fetch_weather(city: str) -> dict[str, Any] | None:
    key = city.lower()
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    url = _WTTR.format(city=city.replace(" ", "+"))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers={"User-Agent": "curl/8.0"})
        if r.status_code != 200:
            logger.warning(
                "wttr.in returned %s for %s", r.status_code, city,
                extra={"event": "WEATHER_HTTP", "city": city},
            )
            return None
        d = r.json()
    except Exception as exc:
        logger.info(
            "weather fetch failed",
            extra={"event": "WEATHER_FETCH_FAIL", "city": city,
                   "reason": str(exc)},
        )
        return None

    try:
        cur = d["current_condition"][0]
        area = (
            d.get("nearest_area", [{}])[0]
            .get("areaName", [{}])[0]
            .get("value", city)
        )

        forecast: list[dict[str, Any]] = []
        for day in (d.get("weather") or [])[:3]:
            forecast.append({
                "date": day.get("date"),
                "max_c": int(day.get("maxtempC", 0) or 0),
                "min_c": int(day.get("mintempC", 0) or 0),
                "avg_c": int(day.get("avgtempC", 0) or 0),
                "condition": (
                    day.get("hourly", [{}])[4]
                    .get("weatherDesc", [{}])[0]
                    .get("value", "")
                ),
                "chance_of_rain_pct": int(
                    day.get("hourly", [{}])[4]
                    .get("chanceofrain", 0) or 0
                ),
            })

        result = {
            "city": area,
            "current": {
                "temp_c": int(cur.get("temp_C", 0)),
                "feels_like_c": int(cur.get("FeelsLikeC", 0)),
                "condition": (
                    cur.get("weatherDesc", [{}])[0].get("value", "")
                ),
                "humidity_pct": int(cur.get("humidity", 0)),
                "wind_kmph": int(cur.get("windspeedKmph", 0)),
                "uv_index": int(cur.get("uvIndex", 0)),
            },
            "forecast": forecast,
            "source": "wttr.in",
        }
        _cache[key] = (now, result)
        logger.info(
            "weather fetched",
            extra={"event": "WEATHER_OK", "city": area,
                   "temp_c": result["current"]["temp_c"]},
        )
        return result
    except Exception as exc:
        logger.exception(
            "weather parse failed",
            extra={"event": "WEATHER_PARSE_FAIL", "city": city},
        )
        return None


__all__ = ["GetWeatherTool"]