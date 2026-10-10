"""Tests for the stored charging history."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "clever_api"))

from clever.history import merge_history
from clever.models import ConsumptionRecord, EnergySurcharge

NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)
SURCHARGE = EnergySurcharge(start=None, end=None, price_dkk_per_kwh=0.52)


def record(day: int, kwh: float, point: str = "home-1") -> ConsumptionRecord:
    return ConsumptionRecord(
        charge_point_id=point,
        connector_id=1,
        start=datetime(2026, 10, day, 20, tzinfo=UTC),
        stop=datetime(2026, 10, day, 23, tzinfo=UTC),
        kwh=kwh,
    )


class HistoryTests(unittest.TestCase):
    def test_first_merge_stores_sessions_and_rate(self) -> None:
        history, changed = merge_history(
            None, [record(2, 30), record(1, 12.5, "public-9")], SURCHARGE, "home-1", NOW
        )
        self.assertTrue(changed)
        self.assertEqual([s["kwh"] for s in history["sessions"]], [12.5, 30])
        self.assertEqual([s["home"] for s in history["sessions"]], [False, True])
        self.assertEqual(history["surcharges"], {"2026-10": 0.52})

    def test_repeated_merge_changes_nothing(self) -> None:
        history, _ = merge_history(None, [record(2, 30)], SURCHARGE, "home-1", NOW)
        again, changed = merge_history(
            history, [record(2, 30)], SURCHARGE, "home-1", NOW
        )
        self.assertFalse(changed)
        self.assertEqual(again, history)

    def test_sessions_missing_from_a_later_response_are_kept(self) -> None:
        history, _ = merge_history(
            None, [record(1, 10), record(2, 30)], SURCHARGE, "home-1", NOW
        )
        later, changed = merge_history(
            history, [record(3, 5)], SURCHARGE, "home-1", NOW
        )
        self.assertTrue(changed)
        self.assertEqual([s["kwh"] for s in later["sessions"]], [10, 30, 5])

    def test_corrected_energy_replaces_the_stored_session(self) -> None:
        history, _ = merge_history(None, [record(2, 30)], SURCHARGE, "home-1", NOW)
        later, changed = merge_history(
            history, [record(2, 31.2)], SURCHARGE, "home-1", NOW
        )
        self.assertTrue(changed)
        self.assertEqual([s["kwh"] for s in later["sessions"]], [31.2])

    def test_each_month_keeps_its_own_surcharge_rate(self) -> None:
        history, _ = merge_history(
            {"sessions": [], "surcharges": {"2026-09": 0.44}},
            [],
            SURCHARGE,
            None,
            NOW,
        )
        self.assertEqual(history["surcharges"], {"2026-09": 0.44, "2026-10": 0.52})


if __name__ == "__main__":
    unittest.main()
