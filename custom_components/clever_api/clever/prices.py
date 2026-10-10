"""Day-ahead electricity prices from Energi Data Service.

Used to price home charging hour by hour. The dataset holds quarter-hourly
prices in DKK/MWh without VAT for the two Danish price areas.
"""

from __future__ import annotations

from calendar import timegm
from time import gmtime, strftime, strptime
from typing import Any

from aiohttp import ClientSession

DATASET_URL = "https://api.energidataservice.dk/dataset/DayAheadPrices"
# The Great Belt separates the two price areas; Bornholm belongs to the eastern one.
GREAT_BELT_LONGITUDE = 10.95


def price_area_for(longitude: float | None) -> str:
    """Return the Danish price area for a longitude: DK1 west of the Great Belt, else DK2."""
    if longitude is None:
        return "DK1"
    return "DK1" if longitude < GREAT_BELT_LONGITUDE else "DK2"


def hourly_prices(records: list[dict[str, Any]]) -> dict[str, float]:
    """Average quarter-hourly records into DKK/kWh per UTC hour, keyed by epoch seconds."""
    quarters: dict[int, list[float]] = {}
    for record in records:
        price = record.get("DayAheadPriceDKK")
        stamp = record.get("TimeUTC")
        if price is None or not stamp:
            continue
        hour = timegm(strptime(str(stamp)[:13], "%Y-%m-%dT%H"))
        quarters.setdefault(hour, []).append(float(price) / 1000)
    return {
        str(hour): round(sum(values) / len(values), 4)
        for hour, values in quarters.items()
    }


def fetch_start(hours: dict[str, float], first_day: str) -> str:
    """Return the first day to request: two days before the newest stored hour.

    Tomorrow's prices are published in the afternoon, so the tail is read again.
    """
    if not hours:
        return first_day
    return strftime("%Y-%m-%d", gmtime(max(int(hour) for hour in hours) - 2 * 86400))


def price_series(hours: dict[str, float]) -> dict[str, Any]:
    """Return ``{"start": epoch, "prices": [...]}`` with one entry per hour and ``None`` in gaps."""
    if not hours:
        return {"start": 0, "prices": []}
    stamps = sorted(int(hour) for hour in hours)
    return {
        "start": stamps[0],
        "prices": [
            hours.get(str(hour)) for hour in range(stamps[0], stamps[-1] + 3600, 3600)
        ],
    }


async def fetch_day_ahead(
    session: ClientSession, area: str, start_day: str, end_day: str
) -> list[dict[str, Any]]:
    """Fetch day-ahead price records for ``area`` between two ``YYYY-MM-DD`` days (UTC)."""
    params = {
        "start": start_day,
        "end": end_day,
        "limit": "0",
        "timezone": "utc",
        "filter": f'{{"PriceArea":["{area}"]}}',
    }
    async with session.get(DATASET_URL, params=params, timeout=30) as response:
        response.raise_for_status()
        payload = await response.json()
    return list(payload.get("records") or [])
