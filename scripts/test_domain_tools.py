"""Test get_gold_rate and get_weather end to end."""
from __future__ import annotations
import asyncio


async def main() -> None:
    from app.tools.builtin.gold_rate import GetGoldRateTool
    from app.tools.builtin.weather import GetWeatherTool

    print("=== GOLD RATE (per gram) ===")
    gold = GetGoldRateTool()
    r = await gold.execute(unit="gram")
    print(f"  source: {r['source']}")
    print(f"  24K: ₹{r['gold']['24k_base']}  (with GST: ₹{r['gold']['24k_with_gst']})")
    print(f"  22K: ₹{r['gold']['22k_base']}")
    print(f"  18K: ₹{r['gold']['18k_base']}")
    print(f"  silver: ₹{r['silver']['base']}/g")
    print()

    print("=== GOLD RATE (per sovereign) ===")
    r = await gold.execute(unit="sovereign")
    print(f"  22K per sovereign: ₹{r['gold']['22k_base']}")
    print()

    print("=== WEATHER (default = Chennai) ===")
    weather = GetWeatherTool(default_city="Chennai")
    r = await weather.execute()
    print(f"  city: {r['city']}")
    print(f"  temp: {r['current']['temp_c']}°C (feels {r['current']['feels_like_c']}°C)")
    print(f"  condition: {r['current']['condition']}")
    print(f"  humidity: {r['current']['humidity_pct']}%")
    print(f"  wind: {r['current']['wind_kmph']} km/h")
    print(f"  forecast:")
    for day in r["forecast"]:
        print(f"    {day['date']}: {day['min_c']}-{day['max_c']}°C, "
              f"{day['condition']}, {day['chance_of_rain_pct']}% rain")
    print()

    print("=== WEATHER (Mumbai) ===")
    r = await weather.execute(city="Mumbai")
    print(f"  {r['city']}: {r['current']['temp_c']}°C, {r['current']['condition']}")


if __name__ == "__main__":
    asyncio.run(main())