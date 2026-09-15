"""Current weather + short forecast from Open-Meteo (free, no API key), cached in memory."""
import logging
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Query

log = logging.getLogger("startpage.weather")
router = APIRouter(prefix="/api/weather", tags=["weather"])

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TTL = 15 * 60

_geo_cache: dict[str, dict] = {}
_cache: dict[tuple, tuple[float, dict]] = {}
LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def _at(arr, i):
    """`arr[i]` or None — Open-Meteo can return ragged/missing daily arrays."""
    return arr[i] if isinstance(arr, list) and i < len(arr) else None


async def _geocode(client: httpx.AsyncClient, location: str) -> dict:
    m = LATLON.match(location)
    if m:
        return {"name": location.strip(), "latitude": float(m[1]), "longitude": float(m[2])}
    key = location.strip().lower()
    if key not in _geo_cache:
        # "Paris, FR" -> search "Paris", prefer results in country/admin matching the rest.
        name, _, hint = location.partition(",")
        resp = await client.get(GEOCODE_URL, params={"name": name.strip(), "count": 10, "format": "json"})
        results = resp.raise_for_status().json().get("results") or []
        if not results:
            raise HTTPException(404, f"Location not found: {location}")
        hint = hint.strip().lower()
        if hint:
            fields = ("country_code", "country", "admin1")
            results.sort(key=lambda r: not any(str(r.get(f, "")).lower().startswith(hint) for f in fields))
        r = results[0]
        label = ", ".join(p for p in (r.get("name"), r.get("admin1") or r.get("country")) if p)
        _geo_cache[key] = {"name": label, "latitude": r["latitude"], "longitude": r["longitude"]}
    return _geo_cache[key]


@router.get("")
async def weather(
    location: str = Query(..., min_length=1),
    units: str = Query("metric", pattern="^(metric|imperial)$"),
    days: int = Query(3, ge=0, le=7),
):
    key = (location.strip().lower(), units, days)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            place = await _geocode(client, location)
            params = {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": max(days, 1),
            }
            if units == "imperial":
                params.update(temperature_unit="fahrenheit", wind_speed_unit="mph")
            data = (await client.get(FORECAST_URL, params=params)).raise_for_status().json()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("weather fetch failed for %s: %s", location, exc)
        if hit:  # serve stale data rather than nothing
            return hit[1]
        raise HTTPException(502, f"Weather service unavailable: {exc}")

    cur, daily = data.get("current", {}), data.get("daily", {})
    result = {
        "location": place["name"],
        "units": units,
        "fetched_at": time.time(),
        "current": {
            "temp": cur.get("temperature_2m"),
            "feels_like": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"),
            "wind": cur.get("wind_speed_10m"),
            "code": cur.get("weather_code"),
            "is_day": bool(cur.get("is_day", 1)),
        },
        "daily": [
            {
                "date": date,
                "code": _at(daily.get("weather_code"), i),
                "max": _at(daily.get("temperature_2m_max"), i),
                "min": _at(daily.get("temperature_2m_min"), i),
                "precip": _at(daily.get("precipitation_probability_max"), i),
            }
            for i, date in enumerate(daily.get("time", [])[:days])
        ],
    }
    _cache[key] = (time.time(), result)
    return result
