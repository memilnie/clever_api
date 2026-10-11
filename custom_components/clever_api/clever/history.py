"""Charging history kept across restarts.

Clever's consumption endpoint returns completed sessions, and the surcharge
endpoint only the current month's rate. Both are merged into a small document
so that earlier sessions and rates stay available for statistics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import ConsumptionRecord, EnergySurcharge


def _session_key(session: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(session.get("charge_point_id") or ""),
        int(session.get("connector_id") or 0),
        str(session.get("start") or ""),
    )


# A single poll cannot plausibly add more than this; larger jumps are a reset or a new session.
MAX_STEP_KWH = 30.0


def record_progress(
    history: dict[str, Any],
    session_start: datetime,
    consumed_wh: float,
    now: datetime,
) -> bool:
    """Add the energy charged since the last poll to the hour it was charged in.

    Clever only reports when a session started and ended. Following the
    running total while the car charges gives the hours the energy was
    actually delivered in. The first poll of a session only sets the baseline,
    so a session that was already under way is recorded from that point on.
    Returns whether anything was stored.
    """
    profiles = history.setdefault("profiles", {})
    key = session_start.isoformat()
    profile = profiles.setdefault(key, {"hours": {}, "last_wh": None})
    previous = profile["last_wh"]
    if previous is not None and consumed_wh == previous:
        return False
    profile["last_wh"] = consumed_wh
    if previous is None:
        # Energy before the first poll cannot be placed in time, unless the session has just begun.
        if consumed_wh > 0 and (now - session_start).total_seconds() <= 600:
            previous = 0.0
        else:
            return True
    step_kwh = (consumed_wh - previous) / 1000
    if 0 < step_kwh <= MAX_STEP_KWH:
        hour = str(int(now.timestamp()) // 3600 * 3600)
        profile["hours"][hour] = round(profile["hours"].get(hour, 0) + step_kwh, 4)
    return True


def session_profiles(history: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Return recorded energy per hour, keyed by the session's start in epoch seconds."""
    result: dict[str, dict[str, float]] = {}
    for start, profile in (history.get("profiles") or {}).items():
        hours = profile.get("hours") or {}
        if hours:
            result[str(int(datetime.fromisoformat(start).timestamp()))] = hours
    return result


def merge_history(
    stored: dict[str, Any] | None,
    records: list[ConsumptionRecord],
    surcharge: EnergySurcharge,
    home_charge_box_id: str | None,
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    """Return ``(history, changed)`` with new sessions and this month's rate added.

    Sessions are identified by charge point, connector and start time. A stored
    session is replaced when Clever later reports a different energy or stop
    time for it, and is never dropped when it falls out of Clever's response.
    """
    history = {
        **(stored or {}),
        "sessions": list((stored or {}).get("sessions") or []),
        "surcharges": dict((stored or {}).get("surcharges") or {}),
    }
    by_key = {_session_key(session): session for session in history["sessions"]}
    changed = False
    for record in records:
        if record.start is None:
            continue
        session = {
            "start": record.start.isoformat(),
            "stop": record.stop.isoformat() if record.stop else None,
            "kwh": round(record.kwh, 3),
            "charge_point_id": record.charge_point_id,
            "connector_id": record.connector_id,
            "home": bool(
                home_charge_box_id and record.charge_point_id == home_charge_box_id
            ),
        }
        key = _session_key(session)
        if by_key.get(key) != session:
            by_key[key] = session
            changed = True
    month = now.strftime("%Y-%m")
    rate = round(surcharge.price_dkk_per_kwh, 4)
    if history["surcharges"].get(month) != rate:
        history["surcharges"][month] = rate
        changed = True
    history["sessions"] = sorted(by_key.values(), key=lambda item: item["start"])
    return history, changed
