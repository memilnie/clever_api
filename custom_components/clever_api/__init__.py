"""Support for Clever subscriptions and home chargers."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .clever.flex import DEPARTURE_TIME, REQUIRED_ENERGY, flex_settings
from .clever.migration import migrate_entity_unique_ids
from .const import (
    CONF_CONFIG_ENTRY_ID,
    CONF_DEPT_TIME,
    CONF_DESIRED_RANGE,
    CONF_PHASE_COUNT,
    CONF_READ_ONLY,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SERVICE_DISABLE_FLEX,
    SERVICE_ENABLE_FLEX,
)
from .coordinator import CleverApiUpdateCoordinator, history_store

READ_PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONTROL_PLATFORMS = [Platform.SWITCH]

_TIME = vol.Match(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_ENERGY = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))

SERVICE_ENABLE_FLEX_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(CONF_CONFIG_ENTRY_ID): str,
            vol.Exclusive(DEPARTURE_TIME, "departure"): _TIME,
            vol.Exclusive(REQUIRED_ENERGY, "energy"): _ENERGY,
            # Field names from before 0.5.0, kept so existing automations stay valid.
            vol.Exclusive(CONF_DEPT_TIME, "departure"): _TIME,
            vol.Exclusive(CONF_DESIRED_RANGE, "energy"): _ENERGY,
            # The v6 API no longer exposes phase count as a charging-profile setting.
            vol.Optional(CONF_PHASE_COUNT): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=3)
            ),
        }
    ),
    cv.has_at_least_one_key(DEPARTURE_TIME, CONF_DEPT_TIME),
    cv.has_at_least_one_key(REQUIRED_ENERGY, CONF_DESIRED_RANGE),
)

SERVICE_DISABLE_FLEX_SCHEMA = vol.Schema({vol.Optional(CONF_CONFIG_ENTRY_ID): str})


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate authentication data and entity unique IDs."""
    if entry.version >= 3:
        return True
    _migrate_entity_unique_ids(hass, entry)
    data = dict(entry.data)
    if entry.version < 2:
        for obsolete_key in ("api_key", "api_token", "url"):
            data.pop(obsolete_key, None)
    hass.config_entries.async_update_entry(entry, data=data, version=3)
    return True


def _migrate_entity_unique_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Keep existing entity IDs while adopting account-scoped unique IDs."""
    customer_id = entry.data.get("user_id")
    if not customer_id:
        return

    migrations: dict[tuple[str, str], str] = {
        ("binary_sensor", "IO"): f"{customer_id}_intelligent_charging",
        ("switch", "preheat"): f"{customer_id}_preheat",
        ("switch", "skip_io"): f"{customer_id}_boost",
    }
    if not entry.data.get("box"):
        migrations.update(
            {
                ("sensor", key): f"{customer_id}_{key}"
                for key in ("kwh_this_month", "energi_tillaeg", "estimated_price")
            }
        )

    registry = er.async_get(hass)
    migrate_entity_unique_ids(
        registry,
        er.async_entries_for_config_entry(registry, entry.entry_id),
        entry.entry_id,
        DOMAIN,
        migrations,
    )


def _coordinator_for_call(
    hass: HomeAssistant, call: ServiceCall
) -> CleverApiUpdateCoordinator:
    """Resolve the requested Clever account, or the only home-charger account."""
    coordinators: dict[str, CleverApiUpdateCoordinator] = hass.data.get(DOMAIN, {})
    if entry_id := call.data.get(CONF_CONFIG_ENTRY_ID):
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            raise HomeAssistantError("The selected Clever account is unavailable")
        if coordinator.read_only:
            raise HomeAssistantError("The selected Clever account is read-only")
        return coordinator

    home_coordinators = [
        coordinator
        for coordinator in coordinators.values()
        if coordinator.data.home_charging_profile is not None
        and not coordinator.read_only
    ]
    if len(home_coordinators) == 1:
        return home_coordinators[0]
    if not home_coordinators:
        raise HomeAssistantError("No Clever home charging profile is available")
    raise HomeAssistantError(
        "Multiple Clever home chargers are configured; select a Clever account"
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Clever from a config entry."""
    if not entry.data.get(CONF_REFRESH_TOKEN):
        raise ConfigEntryAuthFailed("Clever must be reauthenticated with a password")

    coordinator = CleverApiUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    coordinator.platforms = READ_PLATFORMS + (
        [] if entry.data.get(CONF_READ_ONLY, False) else CONTROL_PLATFORMS
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, coordinator.platforms)

    if not coordinator.read_only and not hass.services.has_service(
        DOMAIN, SERVICE_ENABLE_FLEX
    ):

        async def enable_flex(call: ServiceCall) -> None:
            coordinator = _coordinator_for_call(hass, call)
            profile = coordinator.data.home_charging_profile
            if profile is None:
                raise HomeAssistantError("No Clever home charging profile is available")
            departure_time, required_energy = flex_settings(call.data)
            await coordinator.client.set_departure_time(
                profile.profile_id, departure_time
            )
            await coordinator.client.set_power_required(
                profile.profile_id, required_energy
            )
            await coordinator.client.set_charging_profile_enabled(
                profile.profile_id, True
            )
            await coordinator.async_refresh_configuration()

        async def disable_flex(call: ServiceCall) -> None:
            coordinator = _coordinator_for_call(hass, call)
            profile = coordinator.data.home_charging_profile
            if profile is None:
                raise HomeAssistantError("No Clever home charging profile is available")
            await coordinator.client.set_charging_profile_enabled(
                profile.profile_id, False
            )
            await coordinator.async_refresh_configuration()

        hass.services.async_register(
            DOMAIN, SERVICE_ENABLE_FLEX, enable_flex, schema=SERVICE_ENABLE_FLEX_SCHEMA
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_DISABLE_FLEX,
            disable_flex,
            schema=SERVICE_DISABLE_FLEX_SCHEMA,
        )

    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored charging history when the account is removed."""
    await history_store(hass, entry.entry_id).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Clever config entry."""
    coordinator: CleverApiUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, coordinator.platforms
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        # The control actions only exist while an account that may be controlled is loaded.
        if all(other.read_only for other in hass.data[DOMAIN].values()):
            hass.services.async_remove(DOMAIN, SERVICE_ENABLE_FLEX)
            hass.services.async_remove(DOMAIN, SERVICE_DISABLE_FLEX)
        if not hass.data[DOMAIN]:
            del hass.data[DOMAIN]
    return unload_ok
