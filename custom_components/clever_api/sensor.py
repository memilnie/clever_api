"""Sensors for Clever subscriptions and home chargers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .clever.models import CleverApiData
from .const import DOMAIN
from .coordinator import CleverApiUpdateCoordinator
from .entity import CleverApiEntity


@dataclass(frozen=True, kw_only=True)
class CleverSensorDescription(SensorEntityDescription):
    value_fn: Callable[[CleverApiData], Any]
    attributes_fn: Callable[[CleverApiData], dict[str, Any]] | None = None
    requires_home_charger: bool = False


SENSORS = (
    CleverSensorDescription(
        key="kwh_this_month",
        translation_key="energy_this_month",
        name="Energy this month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data.consumption.kwh_this_month,
        attributes_fn=lambda data: {"last_charge": data.consumption.last_charge},
    ),
    CleverSensorDescription(
        key="energi_tillaeg",
        translation_key="energy_surcharge",
        name="Energy surcharge",
        native_unit_of_measurement="DKK/kWh",
        value_fn=lambda data: data.surcharge.price_dkk_per_kwh,
        attributes_fn=lambda data: {"period_end": data.surcharge.end},
    ),
    CleverSensorDescription(
        key="estimated_price",
        translation_key="estimated_price",
        name="Estimated total price this month",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="DKK",
        value_fn=lambda data: data.estimated_total_price,
        attributes_fn=lambda data: {"energy_surcharge": data.energy_surcharge_cost},
    ),
    CleverSensorDescription(
        key="subscription_fee",
        translation_key="subscription_fee",
        name="Monthly subscription fee",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="DKK",
        value_fn=lambda data: data.subscription_fee,
    ),
    CleverSensorDescription(
        key="energy_surcharge_cost",
        translation_key="energy_surcharge_cost",
        name="Energy surcharge this month",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="DKK",
        value_fn=lambda data: data.energy_surcharge_cost,
        attributes_fn=lambda data: {
            "rate_dkk_per_kwh": data.surcharge.price_dkk_per_kwh,
            "energy_kwh": data.consumption.kwh_this_month,
        },
    ),
    CleverSensorDescription(
        key="effective_price_per_kwh",
        translation_key="effective_price_per_kwh",
        name="Effective price per kWh this month",
        icon="mdi:scale-balance",
        native_unit_of_measurement="DKK/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.effective_price_per_kwh_at(dt_util.now()),
        attributes_fn=lambda data: {
            "subscription_fee": data.subscription_fee,
            "energy_surcharge": data.energy_surcharge_cost,
            "energy_kwh": data.consumption.kwh_this_month,
        },
    ),
    CleverSensorDescription(
        key="kwh_this_month_box",
        translation_key="energy_this_month_home",
        name="Energy this month on home charger",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data.consumption.kwh_this_month_box,
        requires_home_charger=True,
    ),
    CleverSensorDescription(
        key="evse_energy",
        translation_key="current_session_energy",
        name="Energy this charging session",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data.active_home_transaction.consumed_wh
            if data.active_home_transaction
            else 0
        ),
        requires_home_charger=True,
    ),
    CleverSensorDescription(
        key="session_target_energy",
        translation_key="session_target_energy",
        name="Target energy this charging session",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda data: (
            data.active_home_transaction.target_energy_kwh
            if data.active_home_transaction
            else None
        ),
        requires_home_charger=True,
    ),
    CleverSensorDescription(
        key="session_progress",
        translation_key="session_progress",
        name="Charging session progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            data.active_home_transaction.progress_percent
            if data.active_home_transaction
            else None
        ),
        requires_home_charger=True,
    ),
    CleverSensorDescription(
        key="session_duration",
        translation_key="session_duration",
        name="Charging session duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda data: (
            data.active_home_transaction.duration_seconds_at(datetime.now(UTC))
            if data.active_home_transaction
            else None
        ),
        requires_home_charger=True,
    ),
    CleverSensorDescription(
        key="session_average_power",
        translation_key="session_average_power",
        name="Average power this charging session",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            data.active_home_transaction.average_power_kw_at(datetime.now(UTC))
            if data.active_home_transaction
            else None
        ),
        requires_home_charger=True,
    ),
    CleverSensorDescription(
        key="session_expected_completion",
        translation_key="session_expected_completion",
        name="Expected charging completion",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: (
            data.active_home_transaction.expected_completion
            if data.active_home_transaction
            else None
        ),
        attributes_fn=lambda data: {
            "charging_started": (
                data.active_home_transaction.charging_start
                if data.active_home_transaction
                else None
            ),
            "planned_departure": (
                data.active_home_transaction.planned_departure
                if data.active_home_transaction
                else None
            ),
            "postponed_until": (
                data.active_home_transaction.postponed_until
                if data.active_home_transaction
                else None
            ),
        },
        requires_home_charger=True,
    ),
    CleverSensorDescription(
        key="vehicle_battery_level",
        translation_key="vehicle_battery_level",
        name="Vehicle battery level",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            data.active_home_transaction.battery_level
            if data.active_home_transaction
            else None
        ),
        attributes_fn=lambda data: {
            "charge_limit": (
                data.active_home_transaction.charge_limit
                if data.active_home_transaction
                else None
            )
        },
        requires_home_charger=True,
    ),
    CleverSensorDescription(
        key="evse_state",
        translation_key="charger_state",
        name="State of charger",
        icon="mdi:ev-station",
        value_fn=lambda data: (
            data.home_chargepoint_state.status
            if data.home_chargepoint_state
            else "Unknown"
        ),
        attributes_fn=lambda data: {
            "last_seen": (
                data.home_chargepoint_state.last_seen
                if data.home_chargepoint_state
                else None
            )
        },
        requires_home_charger=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CleverApiUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    has_home_charger = coordinator.data.home_installation is not None
    async_add_entities(
        CleverApiSensor(coordinator, description)
        for description in SENSORS
        if has_home_charger or not description.requires_home_charger
    )


class CleverApiSensor(CleverApiEntity, SensorEntity):
    """A sensor backed by a Clever coordinator snapshot."""

    entity_description: CleverSensorDescription

    def __init__(
        self,
        coordinator: CleverApiUpdateCoordinator,
        description: CleverSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.data.profile.customer_id}_{description.key}"
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
