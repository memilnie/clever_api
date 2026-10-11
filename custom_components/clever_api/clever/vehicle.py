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
