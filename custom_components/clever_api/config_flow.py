"""Config flow for the Clever API integration."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .clever.clever import CleverClient
from .clever.exceptions import CleverAuthenticationError, CleverError
from .clever.models import ChargingProfile, Installation, Profile
from .clever.prices import price_area_for
from .const import (
    CONF_BOX,
    CONF_BOX_ID,
    CONF_CHARGING_PROFILE_ID,
    CONF_CONNECTOR_ID,
    CONF_FIREBASE_UID,
    CONF_PRICE_AREA,
    CONF_READ_ONLY,
    CONF_REFRESH_TOKEN,
    CONF_SHOW_PANEL,
    CONF_SUBSCRIPTION_FEE,
    CONF_USER_ID,
    DEFAULT_SUBSCRIPTION_FEE,
    DOMAIN,
    LOGGER,
    PRICE_AREAS,
)

PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


def _credentials_schema(email: str | None = None) -> vol.Schema:
    email_key = (
        vol.Required(CONF_EMAIL, default=email)
        if email is not None
        else vol.Required(CONF_EMAIL)
    )
    return vol.Schema(
        {
            email_key: str,
            vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            vol.Optional(
                CONF_SUBSCRIPTION_FEE, default=DEFAULT_SUBSCRIPTION_FEE
            ): vol.Coerce(float),
            # New accounts start without controls; they can be enabled in the options.
            vol.Optional(CONF_READ_ONLY, default=True): bool,
        }
    )


class CleverApiConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Clever configuration and Firebase reauthentication."""

    VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return CleverOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await self._async_login(user_input)
            except CleverAuthenticationError:
                errors["base"] = "invalid_auth"
            except CleverError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - flow must convert unknown errors for UI
                LOGGER.exception("Unexpected error while configuring Clever")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(data[CONF_USER_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=data[CONF_EMAIL], data=data)

        return self.async_show_form(
            step_id="user", data_schema=_credentials_schema(), errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_SUBSCRIPTION_FEE] = entry.data.get(
                CONF_SUBSCRIPTION_FEE, DEFAULT_SUBSCRIPTION_FEE
            )
            user_input[CONF_READ_ONLY] = entry.data.get(CONF_READ_ONLY, False)
            try:
                data = await self._async_login(user_input)
            except CleverAuthenticationError:
                errors["base"] = "invalid_auth"
            except CleverError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - flow must convert unknown errors for UI
                LOGGER.exception("Unexpected error while reauthenticating Clever")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(data[CONF_USER_ID])
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data_updates=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL, default=entry.data.get(CONF_EMAIL)): str,
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    async def _async_login(self, user_input: dict[str, Any]) -> dict[str, Any]:
        client = CleverClient(async_get_clientsession(self.hass))
        try:
            await client.login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
        except CleverAuthenticationError as error:
            LOGGER.warning("Firebase sign-in was rejected: %s", error)
            raise
        try:
            (
                profile_data,
                installation_data,
                charging_profile_data,
            ) = await asyncio.gather(
                client.get_profile(),
                client.get_installations(),
                client.get_charging_profiles(),
            )
        except CleverAuthenticationError as error:
            LOGGER.warning("Clever API rejected the Firebase session: %s", error)
            raise
        profile = Profile.from_api(profile_data)
        installations = [Installation.from_api(item) for item in installation_data]
        charging_profiles = [
            ChargingProfile.from_api(item) for item in charging_profile_data
        ]
        installation = installations[0] if installations else None
        charging_profile = None
        if installation:
            charging_profile = next(
                (item for item in charging_profiles if item.matches(installation)),
                next(
                    (
                        item
                        for item in charging_profiles
                        if item.profile_type.casefold() == "home"
                    ),
                    None,
                ),
            )
        return {
            CONF_EMAIL: user_input[CONF_EMAIL],
            CONF_REFRESH_TOKEN: client.refresh_token,
            CONF_FIREBASE_UID: client.firebase_uid,
            CONF_USER_ID: profile.customer_id,
            CONF_BOX: installation is not None,
            CONF_BOX_ID: installation.charge_box_id if installation else None,
            CONF_CONNECTOR_ID: installation.connector_id if installation else None,
            CONF_CHARGING_PROFILE_ID: (
                charging_profile.profile_id if charging_profile else None
            ),
            CONF_SUBSCRIPTION_FEE: float(
                user_input.get(CONF_SUBSCRIPTION_FEE, DEFAULT_SUBSCRIPTION_FEE)
            ),
            CONF_READ_ONLY: bool(user_input.get(CONF_READ_ONLY, True)),
        }


class CleverOptionsFlow(config_entries.OptionsFlow):
    """Change the subscription fee and control access without reauthentication."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            data = dict(self._config_entry.data)
            before = (
                bool(data.get(CONF_READ_ONLY, False)),
                bool(data.get(CONF_SHOW_PANEL, True)),
            )
            data[CONF_SUBSCRIPTION_FEE] = float(user_input[CONF_SUBSCRIPTION_FEE])
            data[CONF_READ_ONLY] = bool(user_input[CONF_READ_ONLY])
            data[CONF_SHOW_PANEL] = bool(user_input[CONF_SHOW_PANEL])
            data[CONF_PRICE_AREA] = user_input[CONF_PRICE_AREA]
            self.hass.config_entries.async_update_entry(self._config_entry, data=data)
            if (data[CONF_READ_ONLY], data[CONF_SHOW_PANEL]) != before:
                # Switches, control actions and the sidebar panel are added or removed on reload.
                self.hass.config_entries.async_schedule_reload(
                    self._config_entry.entry_id
                )
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SUBSCRIPTION_FEE,
                        default=self._config_entry.data.get(
                            CONF_SUBSCRIPTION_FEE, DEFAULT_SUBSCRIPTION_FEE
                        ),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_READ_ONLY,
                        default=self._config_entry.data.get(CONF_READ_ONLY, False),
                    ): bool,
                    vol.Required(
                        CONF_SHOW_PANEL,
                        default=self._config_entry.data.get(CONF_SHOW_PANEL, True),
                    ): bool,
                    vol.Required(
                        CONF_PRICE_AREA,
                        default=self._config_entry.data.get(CONF_PRICE_AREA)
                        or price_area_for(self.hass.config.longitude),
                    ): vol.In(PRICE_AREAS),
                }
            ),
        )
