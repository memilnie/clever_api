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
from .clever.history import merge_history
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
from .const import (
    CONF_FIREBASE_UID,
    CONF_PRICE_AREA,
    CONF_READ_ONLY,
    CONF_REFRESH_TOKEN,
    CONF_SUBSCRIPTION_FEE,
    DOMAIN,
    LOGGER,
)

SLOW_REFRESH_SECONDS = 60 * 60
HISTORY_STORAGE_VERSION = 1
PRICE_REFRESH_SECONDS = 3 * 60 * 60


def history_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding an account's charging sessions and surcharge rates."""
    return Store(hass, HISTORY_STORAGE_VERSION, f"{DOMAIN}.history.{entry_id}")


def price_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding hourly electricity prices for the charging overview."""
    return Store(hass, 1, f"{DOMAIN}.prices.{entry_id}")


def settings_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding the assumptions entered in the charging panel."""
    return Store(hass, 1, f"{DOMAIN}.charging_settings.{entry_id}")


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
            "spot": {
                **price_series(self._prices.get("hours") or {}),
                "area": self._prices.get("area") or self.price_area,
            },
            "settings": await self._settings_store.async_load() or {},
            "now": time.time(),
        }

    async def async_save_charging_settings(self, settings: dict[str, Any]) -> None:
        await self._settings_store.async_save(settings)

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
        return data
