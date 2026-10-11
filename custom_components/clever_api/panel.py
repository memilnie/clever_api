"""Sidebar panels: the charging overview and the car page."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

STATIC_URL = "/clever_api/frontend"
_STATIC_REGISTERED = f"{DOMAIN}_static_registered"
# URL path, web component, JavaScript file, sidebar title, icon
PANELS = (
    (
        "clever-opladning",
        "clever-charging-panel",
        "clever-charging-panel.js",
        "Opladning",
        "mdi:ev-station",
    ),
    (
        "clever-bil",
        "clever-car-panel",
        "clever-car-panel.js",
        "Bil",
        "mdi:car-electric",
    ),
)


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the panels' JavaScript and add them to the sidebar."""
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
    for url_path, component, script, title, icon in PANELS:
        if url_path in hass.data.get(frontend.DATA_PANELS, {}):
            continue
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name=component,
            frontend_url_path=url_path,
            # The version makes browsers fetch the new file after an update.
            module_url=f"{STATIC_URL}/{script}?v={integration.version}",
            sidebar_title=title,
            sidebar_icon=icon,
            require_admin=False,
            config={},
        )


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the panels from the sidebar."""
    for url_path, *_ in PANELS:
        if url_path in hass.data.get(frontend.DATA_PANELS, {}):
            frontend.async_remove_panel(hass, url_path)
