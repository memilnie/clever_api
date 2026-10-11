"""Constants for Clever API integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "clever_api"

LOGGER = logging.getLogger(__package__)

CONF_URL = "url"
CONF_USER_ID = "user_id"
CONF_FIREBASE_UID = "firebase_uid"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_BOX = "box"
CONF_BOX_ID = "box_id"
CONF_CONNECTOR_ID = "box_connector_id"
CONF_CHARGING_PROFILE_ID = "charging_profile_id"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_SUBSCRIPTION_FEE = "subscription_fee"
CONF_DEPT_TIME = "depature_time"
CONF_DESIRED_RANGE = "desired_range"
CONF_PHASE_COUNT = "phase_count"
CONF_READ_ONLY = "read_only"
CONF_PRICE_AREA = "price_area"
CONF_SHOW_PANEL = "show_panel"
CONF_VEHICLE_SOC = "vehicle_soc_entity"
CONF_VEHICLE_ODOMETER = "vehicle_odometer_entity"
CONF_VEHICLE_REFRESH = "vehicle_refresh"
PRICE_AREAS: Final = ["DK1", "DK2"]

# Clever One with a home charger, per month, from 1 September 2026.
# Without a home charger the fee is 999 DKK.
DEFAULT_SUBSCRIPTION_FEE: Final = 849.0

SERVICE_ENABLE_FLEX = "enable_flex"
SERVICE_DISABLE_FLEX = "disable_flex"
