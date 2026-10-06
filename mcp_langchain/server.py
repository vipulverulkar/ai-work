"""
Weather + Web Search MCP Server built with LangChain 1.0.

Run:
  stdio (for Claude Desktop / MCP clients):
    python server.py
  HTTP (streamable):
    python server.py --http --port 8000
    # -> http://localhost:8000/mcp

Tools exposed over MCP:
  - get_weather(city: str) -> live weather via Open-Meteo (no API key)
  - get_air_quality(city: str) -> AQI + pollutants via Open-Meteo Air Quality API (no API key)
  - search_web(query: str, max_results: int) -> web search via DuckDuckGo (no API key)
  - get_weather_with_sources(city: str) -> weather + web sources combined

The core logic is written as LangChain 1.0 @tool tools first,
then re-exposed as MCP tools (same functions).
"""

import argparse
import asyncio
import sys
from typing import Annotated

import httpx
from langchain_core.tools import tool as langchain_tool
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# LangChain 1.0 tools (core logic)
# ---------------------------------------------------------------------------

WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _city_local_time(tz_name: str) -> str:
    """Current local time for an IANA timezone, no network needed."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:  # noqa: BLE001 - city details are best-effort
        return ""


def _city_details(loc: dict, local_time: str = "") -> list[str]:
    """Format geocoder result as a 'City:' details block."""
    lines = [f"City: {loc.get('name', '')}"]
    region = ", ".join(x for x in (loc.get("admin1"), loc.get("admin2"), loc.get("admin3")) if x)
    if region:
        lines.append(f"  Region: {region}")
    country = loc.get("country", "")
    cc = loc.get("country_code", "")
    if country:
        lines.append(f"  Country: {country}" + (f" ({cc})" if cc else ""))
    lines.append(f"  Coordinates: {loc.get('latitude')}, {loc.get('longitude')}")
    if loc.get("elevation") is not None:
        lines.append(f"  Elevation: {loc['elevation']} m")
    if loc.get("population"):
        lines.append(f"  Population: {loc['population']:,}")
    if loc.get("timezone"):
        lines.append(f"  Timezone: {loc['timezone']}")
    if local_time:
        lines.append(f"  Local time: {local_time}")
    return lines


async def _fetch_weather(city: str) -> str:
    """Shared impl: geocode + forecast via Open-Meteo (free, no key)."""
    city = city.strip()
    if not city:
        return "Error: please provide a non-empty city name."

    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "mcp-langchain-weather/0.1"}) as client:
        # 1. Geocode
        geo = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"},
        )
        geo.raise_for_status()
        results = geo.json().get("results")
        if not results:
            return f"City '{city}' not found. Try e.g. 'London', 'Mumbai', 'New York'."

        loc = results[0]
        lat, lon = loc["latitude"], loc["longitude"]
        name = loc.get("name", city)
        country = loc.get("country", "")
        admin1 = loc.get("admin1", "")

        # 2. Forecast
        wx = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "timezone": "auto",
                "forecast_days": 3,
            },
        )
        wx.raise_for_status()
        data = wx.json()

        local_time = _city_local_time(loc.get('timezone', ''))

    cur = data.get("current", {})
    daily = data.get("daily", {})
    code = cur.get("weather_code")
    desc = WEATHER_CODES.get(code, f"code {code}")

    place = f"{name}" + (f", {admin1}" if admin1 else "") + (f", {country}" if country else "")
    lines = [
        f"Weather in {place} ({lat:.2f}, {lon:.2f}):",
    ]
    lines.extend(_city_details(loc, local_time))
    lines.extend([
        "",
        f"  Now: {cur.get('temperature_2m')}°C (feels like {cur.get('apparent_temperature')}°C), {desc}",
        f"  Humidity: {cur.get('relative_humidity_2m')}%, Wind: {cur.get('wind_speed_10m')} km/h",
        "",
        "Next 3 days:",
    ])
    times = daily.get("time", [])
    for i in range(min(3, len(times))):
        dcode = (daily.get("weather_code") or [None])[i] if daily.get("weather_code") else None
        ddesc = WEATHER_CODES.get(dcode, "")
        lines.append(
            f"  {times[i]}: {ddesc}, "
            f"{daily.get('temperature_2m_min', ['?'])[i]}°C / "
            f"{daily.get('temperature_2m_max', ['?'])[i]}°C, "
            f"rain prob {daily.get('precipitation_probability_max', ['?'])[i]}%"
        )
    lines.append("")
    lines.append("Source: Open-Meteo (open-meteo.com)")
    return "\n".join(lines)


def _web_search_sync(query: str, max_results: int = 5) -> str:
    """Shared impl: DuckDuckGo text search (free, no key) via ddgs."""
    from ddgs import DDGS

    query = query.strip()
    if not query:
        return "Error: please provide a non-empty search query."
    max_results = max(1, min(max_results, 10))

    with DDGS() as ddgs:
        hits = list(ddgs.text(query, max_results=max_results))

    if not hits:
        return f"No web results for '{query}'."
    out = [f"Top {len(hits)} web results for '{query}':", ""]
    for i, h in enumerate(hits, 1):
        out.append(f"{i}. {h.get('title', 'No title')}")
        out.append(f"   {h.get('href', h.get('link', ''))}")
        body = (h.get("body") or "").strip()
        if body:
            out.append(f"   {body[:300]}")
        out.append("")
    return "\n".join(out).strip()


# European AQI bands (2026 scale) for PM2.5 / PM10 / NO2 / O3
def _aqi_band(value: float) -> str:
    if value <= 20:
        return "Good"
    if value <= 40:
        return "Fair"
    if value <= 60:
        return "Poor"
    if value <= 80:
        return "Very poor"
    if value <= 100:
        return "Extremely poor"
    return "Dangerous"


async def _fetch_air_quality(city: str) -> str:
    """Shared impl: geocode + air quality via Open-Meteo Air Quality API (free, no key)."""
    city = city.strip()
    if not city:
        return "Error: please provide a non-empty city name."

    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "mcp-langchain-weather/0.1"}) as client:
        geo = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"},
        )
        geo.raise_for_status()
        results = geo.json().get("results")
        if not results:
            return f"City '{city}' not found. Try e.g. 'London', 'Mumbai', 'New York'."

        loc = results[0]
        lat, lon = loc["latitude"], loc["longitude"]
        name = loc.get("name", city)
        country = loc.get("country", "")

        aq = await client.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "us_aqi,european_aqi,pm2_5,pm10,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone",
            },
        )
        aq.raise_for_status()
        cur = aq.json().get("current", {})

        local_time = _city_local_time(loc.get('timezone', ''))

    place = f"{name}" + (f", {country}" if country else "")
    eur = cur.get("european_aqi")
    band = _aqi_band(eur) if eur is not None else "n/a"
    lines = [
        f"Air quality in {place} ({lat:.2f}, {lon:.2f}):",
    ]
    lines.extend(_city_details(loc, local_time))
    lines.extend([
        "",
        f"  US AQI: {cur.get('us_aqi')}",
        f"  European AQI: {eur} ({band})",
        "",
        "Pollutants (µg/m³):",
        f"  PM2.5: {cur.get('pm2_5')}",
        f"  PM10: {cur.get('pm10')}",
        f"  NO2: {cur.get('nitrogen_dioxide')}",
        f"  SO2: {cur.get('sulphur_dioxide')}",
        f"  O3: {cur.get('ozone')}",
        f"  CO: {cur.get('carbon_monoxide')} µg/m³",
        "",
        "Source: Open-Meteo Air Quality API (open-meteo.com)",
    ])
    return "\n".join(lines)


@langchain_tool
async def langchain_get_weather(city: Annotated[str, "City name, e.g. 'Paris' or 'Mumbai'"]) -> str:
    """Get current weather and 3-day forecast for a city. Uses Open-Meteo, no API key needed."""
    return await _fetch_weather(city)


@langchain_tool
async def langchain_get_air_quality(city: Annotated[str, "City name, e.g. 'Delhi' or 'Beijing'"]) -> str:
    """Get current air quality (AQI + pollutants) for a city. Uses Open-Meteo, no API key needed."""
    return await _fetch_air_quality(city)


@langchain_tool
def langchain_web_search(
    query: Annotated[str, "Web search query, e.g. 'Mumbai weather today'"],
    max_results: Annotated[int, "Number of results (1-10)"] = 5,
) -> str:
    """Search the web with DuckDuckGo. No API key needed. Good for weather news/alerts."""
    return _web_search_sync(query, max_results)


# ---------------------------------------------------------------------------
# MCP server (FastMCP from the official MCP Python SDK)
# ---------------------------------------------------------------------------

mcp = FastMCP("weather-search")


@mcp.tool()
async def get_weather(city: str) -> str:
    """Get current weather + 3-day forecast for a city. Example: get_weather('London'). No API key needed."""
    # Reuse the LangChain 1.0 tool logic
    return await _fetch_weather(city)


@mcp.tool()
async def get_air_quality(city: str) -> str:
    """Get current air quality (AQI + pollutant levels) for a city. Example: get_air_quality('Delhi'). No API key needed."""
    return await _fetch_air_quality(city)


@mcp.tool()
def search_web(query: str, max_results: int = 5) -> str:
    """Search the web (DuckDuckGo, no API key). Example: search_web('Tokyo weather typhoon alert')."""
    return _web_search_sync(query, max_results)


@mcp.tool()
async def get_weather_with_sources(city: str) -> str:
    """Get weather for a city PLUS live web search results (news, alerts). Best for a full display."""
    weather = await _fetch_weather(city)
    try:
        loop = asyncio.get_running_loop()
        web = await loop.run_in_executor(None, _web_search_sync, f"{city} weather today", 5)
    except Exception as e:  # noqa: BLE001 - surface search failure gracefully
        web = f"Web search unavailable: {e}"
    return f"{weather}\n\n---\n\n{web}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Weather + web-search MCP server (LangChain 1.0)")
    parser.add_argument("--http", action="store_true", help="Run with streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (default 127.0.0.1)")
    args = parser.parse_args()

    if args.http:
        print(f"Serving weather-search MCP on http://{args.host}:{args.port}/mcp", file=sys.stderr)
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
