"""Tests for the vehicle readings taken around a charging session."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "clever_api"))

from clever.vehicle import add_reading, session_events, vehicle_readings

A = "2026-10-12T17:30:00+00:00"
B = "2026-10-13T18:00:00+00:00"


class EventTests(unittest.TestCase):
    def test_session_found_at_startup_is_only_seen(self) -> None:
        self.assertEqual(session_events(None, (A, True)), [(A, "seen")])
        self.assertEqual(session_events(None, (None, False)), [])

    def test_plugging_in_without_charging(self) -> None:
        self.assertEqual(session_events((None, False), (A, False)), [(A, "connected")])

    def test_plugging_in_and_charging_at_once(self) -> None:
        self.assertEqual(
            session_events((None, False), (A, True)),
            [(A, "connected"), (A, "charging_started")],
        )

    def test_charging_starts_and_stops_within_a_session(self) -> None:
        self.assertEqual(
            session_events((A, False), (A, True)), [(A, "charging_started")]
        )
        self.assertEqual(
            session_events((A, True), (A, False)), [(A, "charging_stopped")]
        )
        self.assertEqual(session_events((A, True), (A, True)), [])

    def test_unplugging(self) -> None:
        self.assertEqual(
            session_events((A, False), (None, False)), [(A, "disconnected")]
        )

    def test_one_session_replaced_by_another_between_polls(self) -> None:
        self.assertEqual(
            session_events((A, False), (B, False)),
            [(A, "disconnected"), (B, "connected")],
        )


class ReadingTests(unittest.TestCase):
    def test_readings_are_kept_per_session(self) -> None:
        history: dict = {}
        when = datetime(2026, 10, 12, 17, 31, tzinfo=UTC)
        self.assertTrue(
            add_reading(
                history, A, "connected", when, {"soc": 41.0, "odometer": 135900.2}
            )
        )
        add_reading(
            history, A, "charging_stopped", when, {"soc": 80.0, "odometer": None}
        )
        readings = vehicle_readings(history)[
            str(int(datetime.fromisoformat(A).timestamp()))
        ]
        self.assertEqual(
            [r["event"] for r in readings], ["connected", "charging_stopped"]
        )
        self.assertEqual(readings[0]["soc"], 41.0)
        self.assertNotIn("odometer", readings[1])
        self.assertEqual(readings[0]["time"], int(when.timestamp()))

    def test_a_reading_without_values_is_not_stored(self) -> None:
        history: dict = {}
        when = datetime(2026, 10, 12, 17, 31, tzinfo=UTC)
        self.assertFalse(add_reading(history, A, "connected", when, {"soc": None}))
        self.assertEqual(history.get("vehicle", {}).get(A, []), [])


if __name__ == "__main__":
    unittest.main()
