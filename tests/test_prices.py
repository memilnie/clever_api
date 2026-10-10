"""Tests for the electricity price helpers."""

from __future__ import annotations

import sys
import unittest
from calendar import timegm
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "clever_api"))

from clever.prices import fetch_start, hourly_prices, price_area_for, price_series

MIDNIGHT = timegm((2026, 9, 1, 0, 0, 0))


class PriceTests(unittest.TestCase):
    def test_quarter_hours_are_averaged_into_kwh_prices(self) -> None:
        records = [
            {"TimeUTC": f"2026-09-01T00:{minute:02d}:00", "DayAheadPriceDKK": price}
            for minute, price in ((0, 1000.0), (15, 1200.0), (30, 800.0), (45, 1000.0))
        ] + [{"TimeUTC": "2026-09-01T01:00:00", "DayAheadPriceDKK": 500.0}]
        self.assertEqual(
            hourly_prices(records), {str(MIDNIGHT): 1.0, str(MIDNIGHT + 3600): 0.5}
        )

    def test_records_without_a_price_are_skipped(self) -> None:
        records = [
            {"TimeUTC": "2026-09-01T00:00:00", "DayAheadPriceDKK": None},
            {"TimeUTC": "2026-09-01T00:15:00", "DayAheadPriceDKK": 900.0},
        ]
        self.assertEqual(hourly_prices(records), {str(MIDNIGHT): 0.9})

    def test_series_is_contiguous_with_gaps_marked(self) -> None:
        series = price_series({str(MIDNIGHT): 1.0, str(MIDNIGHT + 2 * 3600): 0.5})
        self.assertEqual(series, {"start": MIDNIGHT, "prices": [1.0, None, 0.5]})
        self.assertEqual(price_series({}), {"start": 0, "prices": []})

    def test_first_fetch_starts_at_the_first_day(self) -> None:
        self.assertEqual(fetch_start({}, "2026-01-01"), "2026-01-01")

    def test_later_fetches_reread_the_last_two_days(self) -> None:
        newest = timegm((2026, 9, 10, 21, 0, 0))
        self.assertEqual(fetch_start({str(newest): 1.0}, "2026-01-01"), "2026-09-08")

    def test_price_area_follows_the_great_belt(self) -> None:
        self.assertEqual(price_area_for(9.45), "DK1")  # Viborg
        self.assertEqual(price_area_for(10.4), "DK1")  # Odense
        self.assertEqual(price_area_for(12.57), "DK2")  # Copenhagen
        self.assertEqual(price_area_for(14.9), "DK2")  # Bornholm
        self.assertEqual(price_area_for(None), "DK1")


if __name__ == "__main__":
    unittest.main()
