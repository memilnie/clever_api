"""Input handling for the intelligent-charging action."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEPARTURE_TIME = "departure_time"
REQUIRED_ENERGY = "required_energy"
# Field names used before 0.5.0. They remain accepted so existing automations keep working.
LEGACY_DEPARTURE_TIME = "depature_time"
LEGACY_REQUIRED_ENERGY = "desired_range"


def flex_settings(data: Mapping[str, Any]) -> tuple[str, int]:
    """Return ``(departure time, required energy in kWh)`` from action data."""
    departure = data.get(DEPARTURE_TIME, data.get(LEGACY_DEPARTURE_TIME))
    energy = data.get(REQUIRED_ENERGY, data.get(LEGACY_REQUIRED_ENERGY))
    if departure is None:
        raise ValueError("A departure time is required")
    if energy is None:
        raise ValueError("The required energy is required")
    return str(departure), int(energy)
