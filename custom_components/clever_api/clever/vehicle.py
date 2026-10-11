"""Vehicle readings taken when a home charging session changes state.

Clever knows when the car is connected and charging; the car knows its battery
level and odometer. Reading the car at those moments ties the energy Clever
measured to what happened in the battery and to the distance driven.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# (session key, whether the car is charging); the key is the session's start time.
SessionState = tuple[str | None, bool]


def session_events(
    previous: SessionState | None, current: SessionState
) -> list[tuple[str, str]]:
    """Return ``(session key, event)`` pairs for the change between two polls."""
    key, charging = current
    if previous is None:
        # First poll after start-up: a session found now did not begin now.
        return [(key, "seen")] if key else []
    old_key, old_charging = previous
    events: list[tuple[str, str]] = []
    if old_key and old_key != key:
        events.append((old_key, "disconnected"))
    if key and key != old_key:
        events.append((key, "connected"))
        if charging:
            events.append((key, "charging_started"))
    elif key and charging != old_charging:
        events.append((key, "charging_started" if charging else "charging_stopped"))
    return events


def add_reading(
    history: dict[str, Any],
    session_key: str,
    event: str,
    when: datetime,
    values: dict[str, float | None],
) -> bool:
    """Store a reading for a session. Returns ``False`` when the car gave no values."""
    known = {name: value for name, value in values.items() if value is not None}
    if not known:
        return False
    readings = history.setdefault("vehicle", {}).setdefault(session_key, [])
    readings.append({"event": event, "time": when.isoformat(), **known})
    return True


DRIVING_FIELDS = (
    "distance",
    "total_consumed",
    "engine_consumption",
    "climate_consumption",
    "onboard_electronics_consumption",
    "battery_care_consumption",
    "regenerated_energy",
)


def daily_driving(attributes: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Pick per-day driving statistics out of a sensor's attributes.

    The Hyundai/Kia integration publishes the last month of driving as
    ``{"2026-10-08": {"distance": 659, "total_consumed": 114538, ...}}`` with
    energy in Wh and distance in km. Anything else is ignored.
    """
    days: dict[str, dict[str, float]] = {}
    for key, value in attributes.items():
        if (
            len(str(key)) != 10
            or str(key)[4] != "-"
            or str(key)[7] != "-"
            or not isinstance(value, dict)
            or "distance" not in value
        ):
            continue
        day = {
            field: float(value[field])
            for field in DRIVING_FIELDS
            if isinstance(value.get(field), (int, float))
        }
        if day:
            days[str(key)] = day
    return days


def merge_driving(history: dict[str, Any], days: dict[str, dict[str, float]]) -> bool:
    """Keep the car's daily statistics; the car itself only remembers about a month."""
    stored = history.setdefault("driving", {})
    changed = False
    for day, values in days.items():
        if stored.get(day) != values:
            stored[day] = values
            changed = True
    return changed


def log_odometer(history: dict[str, Any], day: str, kilometres: float | None) -> bool:
    """Keep the latest odometer reading of each day."""
    if kilometres is None:
        return False
    log = history.setdefault("odometer", {})
    if log.get(day) == kilometres:
        return False
    log[day] = kilometres
    return True


def vehicle_readings(history: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return the readings keyed by the session's start in epoch seconds."""
    result: dict[str, list[dict[str, Any]]] = {}
    for start, readings in (history.get("vehicle") or {}).items():
        result[str(int(datetime.fromisoformat(start).timestamp()))] = [
            {
                **reading,
                "time": int(datetime.fromisoformat(reading["time"]).timestamp()),
            }
            for reading in readings
        ]
    return result
