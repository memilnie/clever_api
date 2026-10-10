"""WebSocket commands used by the charging panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import CleverApiUpdateCoordinator

_NUMBER = vol.Any(None, bool, vol.All(vol.Coerce(float), vol.Range(min=0, max=1e7)))
SETTINGS_SCHEMA = vol.Schema(
    {
        str: vol.Any(
            _NUMBER,
            # Per-month overrides: {"2026-09": {"fee": 849, "surcharge": 0.44}}
            {
                vol.Match(r"^\d{4}-\d{2}$"): {
                    vol.In(["fee", "surcharge", "refund"]): _NUMBER
                }
            },
        )
    }
)


def _coordinator(
    hass: HomeAssistant, entry_id: str | None
) -> CleverApiUpdateCoordinator | None:
    coordinators: dict[str, CleverApiUpdateCoordinator] = hass.data.get(DOMAIN, {})
    if entry_id:
        return coordinators.get(entry_id)
    return next(iter(coordinators.values()), None)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "clever_api/charging/get",
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_get_charging(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return sessions, surcharge rates, hourly prices and saved assumptions."""
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No Clever account is loaded")
        return
    connection.send_result(msg["id"], await coordinator.async_charging_overview())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "clever_api/charging/save_settings",
        vol.Optional("entry_id"): str,
        vol.Required("settings"): SETTINGS_SCHEMA,
    }
)
@websocket_api.async_response
async def ws_save_settings(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Save the assumptions entered in the charging panel."""
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No Clever account is loaded")
        return
    await coordinator.async_save_charging_settings(msg["settings"])
    connection.send_result(msg["id"])


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_charging)
    websocket_api.async_register_command(hass, ws_save_settings)
