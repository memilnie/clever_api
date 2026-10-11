"""Data coordinator for the Clever API integration."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .clever.clever import CleverClient
from .clever.exceptions import (
    CleverApiError,
    CleverAuthenticationError,
    CleverConnectionError,
)
from .clever.history import merge_history, record_progress, session_profiles
from .clever.models import (
    ActiveTransaction,
    ChargePointState,
    ChargingProfile,
    CleverApiData,
    ConsumptionRecord,
    EnergySurcharge,
    Installation,
    Profile,
    summarize_consumption,
)
from .clever.polling import ACTIVE_INTERVAL, next_interval
from .clever.prices import (
    fetch_day_ahead,
    fetch_start,
    hourly_prices,
    price_area_for,
    price_series,
)
from .clever.vehicle import (
    SessionState,
    add_reading,
    daily_driving,
    log_odometer,
    merge_driving,
    session_events,
    vehicle_readings,
)
from .const import (
    CONF_FIREBASE_UID,
    CONF_PRICE_AREA,
    CONF_READ_ONLY,
    CONF_REFRESH_TOKEN,
    CONF_SUBSCRIPTION_FEE,
    CONF_VEHICLE_ODOMETER,
    CONF_VEHICLE_REFRESH,
    CONF_VEHICLE_SOC,
    DOMAIN,
    LOGGER,
)

SLOW_REFRESH_SECONDS = 60 * 60
HISTORY_STORAGE_VERSION = 1
PRICE_REFRESH_SECONDS = 3 * 60 * 60
# A forced refresh wakes the car, so it is asked at most this often.
VEHICLE_REFRESH_MIN_SECONDS = 10 * 60
VEHICLE_REFRESH_TIMEOUT = 180
# The Hyundai/Kia integration is found automatically; other cars are chosen in the options.
KIA_UVO = "kia_uvo"
KIA_UVO_KEYS = {
    "soc": ("_ev_battery_percentage",),
    "odometer": ("__odometer", "_odometer"),
    "range": ("__ev_driving_range",),
    "consumption_30d": ("_power_consumption_30d",),
    "battery_12v": ("_car_battery_percentage",),
    "regenerated": ("_total_power_regenerated",),
    "consumed_90d": ("_total_power_consumed",),
}


def history_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding an account's charging sessions and surcharge rates."""
    return Store(hass, HISTORY_STORAGE_VERSION, f"{DOMAIN}.history.{entry_id}")


def price_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding hourly electricity prices for the charging overview."""
    return Store(hass, 1, f"{DOMAIN}.prices.{entry_id}")


def settings_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding the assumptions entered in the charging panel."""
    return Store(hass, 1, f"{DOMAIN}.charging_settings.{entry_id}")


def car_settings_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding the service intervals entered on the car page."""
    return Store(hass, 1, f"{DOMAIN}.car_settings.{entry_id}")


class CleverApiUpdateCoordinator(DataUpdateCoordinator[CleverApiData]):
    """Fetch slow REST data hourly and live Firestore data at an adaptive pace."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.config_entry = entry
        self.client = CleverClient(
            async_get_clientsession(hass),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
            firebase_uid=entry.data.get(CONF_FIREBASE_UID),
            token_update_callback=self._token_updated,
        )
        self._slow_refresh_at = 0.0
        self._slow: dict[str, Any] | None = None
        self._failures = 0
        self._history_store = history_store(hass, entry.entry_id)
        self._history: dict[str, Any] | None = None
        self._price_store = price_store(hass, entry.entry_id)
        self._prices: dict[str, Any] | None = None
        self._settings_store = settings_store(hass, entry.entry_id)
        self._car_settings_store = car_settings_store(hass, entry.entry_id)
        self._vehicle_previous: SessionState | None = None
        self._vehicle_refreshed_at = 0.0
        # Read-only entries get no switches and are skipped by the control actions.
        self.read_only = bool(entry.data.get(CONF_READ_ONLY, False))
        self.platforms: list[Any] = []
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=ACTIVE_INTERVAL,
        )

    def _token_updated(self, refresh_token: str, firebase_uid: str) -> None:
        if (
            self.config_entry.data.get(CONF_REFRESH_TOKEN) == refresh_token
            and self.config_entry.data.get(CONF_FIREBASE_UID) == firebase_uid
        ):
            return
        data = dict(self.config_entry.data)
        data[CONF_REFRESH_TOKEN] = refresh_token
        data[CONF_FIREBASE_UID] = firebase_uid
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)

    def _record_charging_progress(self, data: CleverApiData) -> None:
        """Follow the running session, so the hours it actually charged in are known."""
        transaction = data.active_home_transaction
        if (
            transaction is None
            or transaction.charging_start is None
            or self._history is None
        ):
            return
        if record_progress(
            self._history,
            transaction.charging_start,
            transaction.consumed_wh,
            dt_util.utcnow(),
        ):
            # Written at most every two minutes while the car charges.
            self._history_store.async_delay_save(lambda: self._history or {}, 120)

    def vehicle_entities(self) -> dict[str, str]:
        """Return the car's battery level, odometer and range sensors, where known."""
        found: dict[str, str] = {}
        registry = er.async_get(self.hass)
        for entity in registry.entities.values():
            if entity.platform != KIA_UVO or entity.disabled:
                continue
            for name, endings in KIA_UVO_KEYS.items():
                if name not in found and str(entity.unique_id).endswith(endings):
                    found[name] = entity.entity_id
        # Sensors chosen in the options take precedence.
        for name, option in (
            ("soc", CONF_VEHICLE_SOC),
            ("odometer", CONF_VEHICLE_ODOMETER),
        ):
            if self.config_entry.data.get(option):
                found[name] = self.config_entry.data[option]
        return found

    def _vehicle_values(self) -> dict[str, float | None]:
        values: dict[str, float | None] = {}
        for name, entity_id in self.vehicle_entities().items():
            state = self.hass.states.get(entity_id)
            try:
                values[name] = float(state.state) if state else None
            except ValueError:
                values[name] = None
        return values

    def _log_vehicle_day(self) -> None:
        """Keep today's odometer reading and the car's daily driving statistics."""
        if self._history is None:
            return
        changed = log_odometer(
            self._history,
            dt_util.now().strftime("%Y-%m-%d"),
            self._vehicle_values().get("odometer"),
        )
        registry = er.async_get(self.hass)
        for entity in registry.entities.values():
            if entity.platform != KIA_UVO or entity.domain != "sensor":
                continue
            state = self.hass.states.get(entity.entity_id)
            if state is not None:
                changed |= merge_driving(self._history, daily_driving(state.attributes))
        if changed:
            self._history_store.async_delay_save(lambda: self._history or {}, 120)

    def _track_vehicle(self, data: CleverApiData) -> None:
        """Read the car when the home session is connected, starts or stops charging, or ends."""
        transaction = data.active_home_transaction
        current: SessionState = (
            transaction.charging_start.isoformat()
            if transaction and transaction.charging_start
            else None,
            bool(transaction and transaction.status.casefold() == "charging"),
        )
        events = session_events(self._vehicle_previous, current)
        self._vehicle_previous = current
        if events and self._history is not None and self.vehicle_entities():
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_read_vehicle(events),
                "clever_api vehicle reading",
            )

    async def _async_read_vehicle(self, events: list[tuple[str, str]]) -> None:
        """Ask the car for fresh values, then store them with the session."""
        fresh = any(event != "seen" for _, event in events)
        if (
            fresh
            and self.config_entry.data.get(CONF_VEHICLE_REFRESH, True)
            and self.hass.services.has_service(KIA_UVO, "force_update")
            and time.monotonic() - self._vehicle_refreshed_at
            > VEHICLE_REFRESH_MIN_SECONDS
        ):
            self._vehicle_refreshed_at = time.monotonic()
            try:
                async with asyncio.timeout(VEHICLE_REFRESH_TIMEOUT):
                    await self.hass.services.async_call(
                        KIA_UVO, "force_update", {}, blocking=True
                    )
            except Exception as error:  # noqa: BLE001 - the cached values are still worth storing
                LOGGER.debug("Could not refresh the vehicle: %s", error)
        values = self._vehicle_values()
        now = dt_util.utcnow()
        assert self._history is not None
        stored = [
            add_reading(self._history, key, event, now, values) for key, event in events
        ]
        if any(stored):
            await self._history_store.async_save(self._history)

    async def _async_save_history(self) -> None:
        """Add newly reported sessions and this month's surcharge rate to the store."""
        assert self._slow is not None
        if self._history is None:
            self._history = await self._history_store.async_load() or {}
        installations: tuple[Installation, ...] = self._slow["installations"]
        self._history, changed = merge_history(
            self._history,
            self._slow["records"],
            self._slow["surcharge"],
            installations[0].charge_box_id if installations else None,
            dt_util.now(),
        )
        if changed:
            await self._history_store.async_save(self._history)

    @property
    def price_area(self) -> str:
        """The configured price area, or the one Home Assistant's location lies in."""
        return self.config_entry.data.get(CONF_PRICE_AREA) or price_area_for(
            self.hass.config.longitude
        )

    async def _async_refresh_prices(self) -> None:
        """Fetch hourly prices from the first session's month until now.

        Only the tail is requested, at most every few hours. A failure never
        fails the Clever update; the overview then lacks the newest prices.
        """
        sessions = (self._history or {}).get("sessions") or []
        if not sessions:
            return
        if self._prices is None:
            self._prices = await self._price_store.async_load() or {}
        first_day = sessions[0]["start"][:7] + "-01"
        area = self.price_area
        if (
            self._prices.get("area") != area
            or self._prices.get("first_day", "9999") > first_day
        ):
            self._prices = {"area": area, "first_day": first_day, "hours": {}}
        if time.time() - self._prices.get("fetched", 0) < PRICE_REFRESH_SECONDS:
            return
        end_day = (dt_util.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d")
        try:
            records = await fetch_day_ahead(
                async_get_clientsession(self.hass),
                area,
                fetch_start(self._prices["hours"], first_day),
                end_day,
            )
        except (ClientError, TimeoutError, ValueError) as error:
            LOGGER.debug("Could not fetch electricity prices: %s", error)
            return
        self._prices["hours"].update(hourly_prices(records))
        self._prices["fetched"] = time.time()
        await self._price_store.async_save(self._prices)

    async def async_charging_overview(self) -> dict[str, Any]:
        """Return what the charging panel needs. Charge point IDs are left out."""
        if self._history is None:
            self._history = await self._history_store.async_load() or {}
        if self._prices is None:
            self._prices = await self._price_store.async_load() or {}
        sessions = []
        for item in self._history.get("sessions") or []:
            start = dt_util.parse_datetime(str(item.get("start")))
            stop = dt_util.parse_datetime(str(item.get("stop"))) or start
            if start is None:
                continue
            sessions.append(
                {
                    "start": int(start.timestamp()),
                    "stop": int(stop.timestamp()),
                    "kwh": float(item.get("kwh") or 0),
                    "home": bool(item.get("home")),
                }
            )
        return {
            "sessions": sessions,
            "surcharges": self._history.get("surcharges") or {},
            "profiles": session_profiles(self._history),
            "vehicle": vehicle_readings(self._history),
            "vehicle_now": self._vehicle_values(),
            "driving": self._history.get("driving") or {},
            "odometer": self._history.get("odometer") or {},
            "car_settings": await self._car_settings_store.async_load() or {},
            "spot": {
                **price_series(self._prices.get("hours") or {}),
                "area": self._prices.get("area") or self.price_area,
            },
            "settings": await self._settings_store.async_load() or {},
            "now": time.time(),
        }

    async def async_save_charging_settings(self, settings: dict[str, Any]) -> None:
        await self._settings_store.async_save(settings)

    async def async_save_car_settings(self, settings: dict[str, Any]) -> None:
        await self._car_settings_store.async_save(settings)

    async def async_refresh_configuration(self) -> None:
        """Refresh REST configuration after a control request."""
        self._slow_refresh_at = 0.0
        await self.async_request_refresh()

    async def _async_update_data(self) -> CleverApiData:
        try:
            if self._slow is None or time.monotonic() >= self._slow_refresh_at:
                (
                    profile_data,
                    installation_data,
                    consumption_data,
                    surcharge_data,
                    charging_profile_data,
                ) = await asyncio.gather(
                    self.client.get_profile(),
                    self.client.get_installations(),
                    self.client.get_consumption_history(),
                    self.client.get_energy_surcharge(),
                    self.client.get_charging_profiles(),
                )
                self._slow = {
                    "profile": Profile.from_api(profile_data),
                    "installations": tuple(
                        Installation.from_api(item) for item in installation_data
                    ),
                    "records": [
                        ConsumptionRecord.from_api(item) for item in consumption_data
                    ],
                    "surcharge": EnergySurcharge.from_api(surcharge_data),
                    "charging_profiles": tuple(
                        ChargingProfile.from_api(item) for item in charging_profile_data
                    ),
                }
                self._slow_refresh_at = time.monotonic() + SLOW_REFRESH_SECONDS
                await self._async_save_history()
                await self._async_refresh_prices()

            chargepoint_data, transaction_data = await asyncio.gather(
                self.client.get_home_chargepoints(),
                self.client.get_active_transactions(),
            )
        except CleverAuthenticationError as error:
            raise ConfigEntryAuthFailed("Clever authentication expired") from error
        except (CleverConnectionError, CleverApiError) as error:
            self._failures += 1
            self.update_interval = next_interval(
                vehicle_active=False, consecutive_failures=self._failures
            )
            raise UpdateFailed(str(error)) from error

        assert self._slow is not None
        installations: tuple[Installation, ...] = self._slow["installations"]
        charge_box_id = installations[0].charge_box_id if installations else None
        local_now = dt_util.now()
        month_start = local_now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        data = CleverApiData(
            profile=self._slow["profile"],
            installations=installations,
            charging_profiles=self._slow["charging_profiles"],
            consumption=summarize_consumption(
                self._slow["records"], dt_util.as_utc(month_start), charge_box_id
            ),
            surcharge=self._slow["surcharge"],
            chargepoint_states=tuple(
                ChargePointState.from_firestore(item) for item in chargepoint_data
            ),
            transactions=tuple(
                ActiveTransaction.from_firestore(item) for item in transaction_data
            ),
            subscription_fee=float(
                self.config_entry.data.get(CONF_SUBSCRIPTION_FEE, 0)
            ),
        )
        self._failures = 0
        self.update_interval = next_interval(
            vehicle_active=data.active_home_transaction is not None
        )
        self._record_charging_progress(data)
        self._track_vehicle(data)
        self._log_vehicle_day()
        return data
