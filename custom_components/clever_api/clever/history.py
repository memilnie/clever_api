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
