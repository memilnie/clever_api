"""Data coordinator for the Clever API integration."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .clever.clever import CleverClient
from .clever.exceptions import (
    CleverApiError,
    CleverAuthenticationError,
    CleverConnectionError,
)
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
from .const import (
    CONF_FIREBASE_UID,
    CONF_READ_ONLY,
    CONF_REFRESH_TOKEN,
    CONF_SUBSCRIPTION_FEE,
    DOMAIN,
    LOGGER,
)

SLOW_REFRESH_SECONDS = 60 * 60


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
