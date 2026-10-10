"""Tests for the polling cadence and action input handling."""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "clever_api"))

from clever.flex import flex_settings
from clever.polling import ACTIVE_INTERVAL, IDLE_INTERVAL, MAX_BACKOFF, next_interval


class PollingTests(unittest.TestCase):
    def test_session_is_followed_every_minute(self) -> None:
        self.assertEqual(next_interval(vehicle_active=True), ACTIVE_INTERVAL)

    def test_idle_charger_is_polled_less_often(self) -> None:
        self.assertEqual(next_interval(vehicle_active=False), IDLE_INTERVAL)
        self.assertGreater(IDLE_INTERVAL, ACTIVE_INTERVAL)

    def test_failures_back_off_and_are_capped(self) -> None:
        waits = [
            next_interval(vehicle_active=True, consecutive_failures=count)
            for count in range(1, 8)
        ]
        self.assertEqual(waits[0], timedelta(minutes=2))
        self.assertEqual(waits[1], timedelta(minutes=4))
        self.assertEqual(waits, sorted(waits))
        self.assertEqual(waits[-1], MAX_BACKOFF)
        self.assertEqual(
            next_interval(vehicle_active=False, consecutive_failures=500), MAX_BACKOFF
        )


class FlexSettingsTests(unittest.TestCase):
    def test_current_field_names(self) -> None:
        self.assertEqual(
            flex_settings({"departure_time": "07:00", "required_energy": 30}),
            ("07:00", 30),
        )

    def test_legacy_field_names_are_still_accepted(self) -> None:
        self.assertEqual(
            flex_settings({"depature_time": "06:30", "desired_range": 12}),
            ("06:30", 12),
        )

    def test_zero_energy_is_a_valid_value(self) -> None:
        self.assertEqual(
            flex_settings({"departure_time": "07:00", "required_energy": 0}),
            ("07:00", 0),
        )

    def test_missing_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            flex_settings({"required_energy": 30})
        with self.assertRaises(ValueError):
            flex_settings({"departure_time": "07:00"})


if __name__ == "__main__":
    unittest.main()
