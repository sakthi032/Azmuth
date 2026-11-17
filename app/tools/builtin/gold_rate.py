"""Live gold and silver rate tool — India-focused.

Primary source: OroPocket (keyless, returns 24K INR/gram directly with
GST breakdown). Fallback: goldprice.dev (purity-specific rates).

Both APIs return INR per gram, so no USD→INR conversion is needed.
Cache is 15 minutes — gold moves, but not every second.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar

import httpx

from app.config.logging_setup import get_logger
from app.core.errors import ProviderUnavailableError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool

logger = get_logger("tools.gold_rate")

_CACHE_TTL = 900  # 15 minutes
_cache: dict[str, Any] = {"data": None, "at": 0.0}

_OROPOCKET = "https://api.oropocket.com/public/prices"
_GOLDPRICE_DEV = "https://api.goldprice.dev/v1/carat?currency=INR"


class GetGoldRateTool(Tool):
    """Fetch current gold + silver rates in INR per gram."""

    name: ClassVar[str] = "get_gold_rate"
    description: ClassVar[str] = (
        "Get the current gold and silver rate in India (INR per gram). "
        "USE THIS for any question about gold rate, gold price, sona "
        "bhav, silver rate, or 'what is today's gold price'. Returns "
        "24K/22K/18K prices with and without GST, plus silver. "
        "Far more accurate than search_web for this data. "
        "Do NOT search the web for gold/silver rates — call this tool."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "unit": {
                "type": "string",
                "enum": ["gram", "sovereign", "10gram"],
                "description": (
                    "Return prices per gram (default), per sovereign "
                    "(8 gram, common in Tamil Nadu), or per 10 grams."
                ),
            },
        },
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def is_available(self) -> bool:
        return True

    async def execute(self, *, unit: str = "gram") -> dict[str, Any]:
        data = await _fetch_rates()
        if data is None:
            raise ProviderUnavailableError(
                "could not fetch gold rate from any provider"
            )

        multiplier = {"gram": 1.0, "sovereign": 8.0, "10gram": 10.0}[unit]

        # 24K base (without GST) is what news reports quote.
        # Buy price = base + GST (what you'd pay at a jeweller).
        base_24k = data["gold_24k_base"]
        gst_24k = data["gold_24k_with_gst"]

        return {
            "source": data["source"],
            "timestamp": data["timestamp"],
            "currency": "INR",
            "unit": unit,
            "gold": {
                "24k_base": round(base_24k * multiplier, 2),
                "24k_with_gst": round(gst_24k * multiplier, 2),
                "22k_base": round(base_24k * 22 / 24 * multiplier, 2),
                "22k_with_gst": round(gst_24k * 22 / 24 * multiplier, 2),
                "18k_base": round(base_24k * 18 / 24 * multiplier, 2),
                "18k_with_gst": round(gst_24k * 18 / 24 * multiplier, 2),
            },
            "silver": {
                "base": round(data["silver_base"] * multiplier, 2),
                "with_gst": round(data["silver_with_gst"] * multiplier, 2),
            },
            "change_24h_pct": data.get("change_24h_pct", 0.0),
        }


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------
async def _fetch_rates() -> dict[str, Any] | None:
    """Return cached rates if fresh, else fetch from providers."""
    now = time.monotonic()
    if _cache["data"] and (now - _cache["at"]) < _CACHE_TTL:
        return _cache["data"]

    data = await _try_oropocket()
    if data is None:
        data = await _try_goldprice_dev()

    if data is not None:
        _cache["data"] = data
        _cache["at"] = now
    return data


async def _try_oropocket() -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(_OROPOCKET)
        if r.status_code != 200:
            logger.warning(
                "oropocket returned %s", r.status_code,
                extra={"event": "GOLD_OROPOCKET_HTTP", "status": r.status_code},
            )
            return None
        payload = r.json()
        gold = payload["data"]["gold"]
        silver = payload["data"]["silver"]

        # OroPocket: `sell` is base (pre-GST), `buy` is with-GST retail.
        base_24k = float(gold["sell"])
        gst_24k = float(gold["buy"])
        silver_base = float(silver["sell"])
        silver_gst = float(silver["buy"])

        change = float(
            gold.get("change24h", {}).get("sell", 0.0) or 0.0
        )

        logger.info(
            "gold rate fetched",
            extra={
                "event": "GOLD_RATE_OK",
                "provider": "oropocket",
                "base_24k": base_24k,
            },
        )
        return {
            "source": "OroPocket",
            "timestamp": payload["data"].get("timestamp", ""),
            "gold_24k_base": base_24k,
            "gold_24k_with_gst": gst_24k,
            "silver_base": silver_base,
            "silver_with_gst": silver_gst,
            "change_24h_pct": change,
        }
    except Exception as exc:
        logger.info(
            "oropocket failed",
            extra={"event": "GOLD_OROPOCKET_FAIL", "reason": str(exc)},
        )
        return None


async def _try_goldprice_dev() -> dict[str, Any] | None:
    """Fallback: goldprice.dev. Note its numbers are usually lower than
    Indian retail because it excludes import duty. We apply a rough
    Indian premium (~15%) so the fallback is in the same ballpark."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(_GOLDPRICE_DEV)
        if r.status_code != 200:
            return None
        d = r.json()
        # goldprice.dev returns prices per gram of PURE gold for each
        # karat (i.e. already purity-adjusted). We need the 24K value
        # as the base for consistency with OroPocket.
        base_24k = float(d["price_gram_24k"])

        # Indian import duty + dealer margin ~15% above international spot.
        india_premium = 1.15
        base_24k *= india_premium
        gst_24k = base_24k * 1.03  # 3% GST

        logger.info(
            "gold rate fetched (fallback)",
            extra={
                "event": "GOLD_RATE_OK",
                "provider": "goldprice.dev",
                "base_24k": base_24k,
            },
        )
        return {
            "source": "goldprice.dev (with India premium)",
            "timestamp": d.get("timestamp", ""),
            "gold_24k_base": base_24k,
            "gold_24k_with_gst": gst_24k,
            "silver_base": 0.0,   # goldprice.dev doesn't return silver
            "silver_with_gst": 0.0,
            "change_24h_pct": 0.0,
        }
    except Exception as exc:
        logger.info(
            "goldprice.dev failed",
            extra={"event": "GOLD_GOLDPRICE_FAIL", "reason": str(exc)},
        )
        return None


__all__ = ["GetGoldRateTool"]