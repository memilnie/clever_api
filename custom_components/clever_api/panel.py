"""Sidebar panel with the charging overview."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

PANEL_URL_PATH = "clever-opladning"
STATIC_URL = "/clever_api/frontend"
_STATIC_REGISTERED = f"{DOMAIN}_static_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the panel's JavaScript and add it to the sidebar."""
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    if not hass.data.get(_STATIC_REGISTERED):
        # A static path stays registered until Home Assistant restarts.
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    STATIC_URL,
                    str(Path(__file__).parent / "frontend"),
                    cache_headers=False,
                )
            ]
        )
        hass.data[_STATIC_REGISTERED] = True
    integration = await async_get_integration(hass, DOMAIN)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="clever-charging-panel",
        frontend_url_path=PANEL_URL_PATH,
        # The version makes browsers fetch the new file after an update.
        module_url=f"{STATIC_URL}/clever-charging-panel.js?v={integration.version}",
        sidebar_title="Opladning",
        sidebar_icon="mdi:ev-station",
        require_admin=False,
        config={},
    )


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the panel from the sidebar."""
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
