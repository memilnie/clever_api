"""Polling cadence for the Clever backends."""

from __future__ import annotations

from datetime import timedelta

ACTIVE_INTERVAL = timedelta(minutes=1)
IDLE_INTERVAL = timedelta(minutes=5)
MAX_BACKOFF = timedelta(minutes=30)


def next_interval(*, vehicle_active: bool, consecutive_failures: int = 0) -> timedelta:
    """Return how long to wait before the next live poll.

    A charging session is followed every minute. With no vehicle on the home
    charger nothing changes quickly, so the backend is asked less often. After
    a failed poll the wait doubles for each consecutive failure, up to
    ``MAX_BACKOFF``, so an outage or a rejected client is not hammered.
    """
    if consecutive_failures > 0:
        return min(MAX_BACKOFF, ACTIVE_INTERVAL * 2 ** min(consecutive_failures, 10))
    return ACTIVE_INTERVAL if vehicle_active else IDLE_INTERVAL
