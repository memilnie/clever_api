"""Tests for the subscription price figures."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "clever_api"))

from clever.models import (
    CleverApiData,
    ConsumptionSummary,
    EnergySurcharge,
    Profile,
    month_fraction,
)


def snapshot(kwh: float, surcharge: float, fee: float = 849.0) -> CleverApiData:
    return CleverApiData(
        profile=Profile("customer", "firebase", "person@example.com", "", ""),
        installations=(),
        charging_profiles=(),
        consumption=ConsumptionSummary(kwh_this_month=kwh),
        surcharge=EnergySurcharge(start=None, end=None, price_dkk_per_kwh=surcharge),
        chargepoint_states=(),
        transactions=(),
        subscription_fee=fee,
    )


class PricingTests(unittest.TestCase):
    def test_month_fraction(self) -> None:
        self.assertEqual(month_fraction(datetime(2026, 9, 1, tzinfo=UTC)), 0)
        self.assertEqual(month_fraction(datetime(2026, 9, 16, tzinfo=UTC)), 0.5)
        # December rolls over into the next year.
        self.assertAlmostEqual(
            month_fraction(datetime(2026, 12, 31, 12, tzinfo=UTC)), 30.5 / 31
        )

    def test_surcharge_and_total(self) -> None:
        data = snapshot(kwh=450, surcharge=0.44)
        self.assertEqual(data.energy_surcharge_cost, 198.0)
        self.assertEqual(data.estimated_total_price, 1047.0)

    def test_no_surcharge_in_cheap_months(self) -> None:
        data = snapshot(kwh=450, surcharge=0)
        self.assertEqual(data.energy_surcharge_cost, 0)
        self.assertEqual(data.estimated_total_price, 849.0)

    def test_effective_price_counts_the_fee_for_the_elapsed_part_of_the_month(
        self,
    ) -> None:
        data = snapshot(kwh=225, surcharge=0.44)
        halfway = datetime(2026, 9, 16, tzinfo=UTC)
        # Half the fee (424.50) plus the surcharge on 225 kWh (99.00), over 225 kWh.
        self.assertEqual(data.effective_price_per_kwh_at(halfway), 2.327)

    def test_effective_price_at_month_end_uses_the_whole_fee(self) -> None:
        data = snapshot(kwh=450, surcharge=0.44)
        end = datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)
        self.assertAlmostEqual(
            data.effective_price_per_kwh_at(end), 1047.0 / 450, places=2
        )

    def test_effective_price_is_unknown_before_the_first_charge(self) -> None:
        self.assertIsNone(
            snapshot(kwh=0, surcharge=0.44).effective_price_per_kwh_at(
                datetime(2026, 9, 16, tzinfo=UTC)
            )
        )


if __name__ == "__main__":
    unittest.main()
