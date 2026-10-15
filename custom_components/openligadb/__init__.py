"""The OpenLigaDB integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENTRY_TYPE,
    CONF_LEAGUE_SHORTCUT,
    CONF_SEASON,
    CONF_TEAM_NAME,
    CONF_TEAM_ID,
    DOMAIN,
)
from .coordinator import OpenLigaDBDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OpenLigaDB from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    league_shortcut = entry.data.get(CONF_LEAGUE_SHORTCUT, "bl1")
    season = str(entry.data.get(CONF_SEASON, dt_util.now().year))
    entry_type = entry.data.get(CONF_ENTRY_TYPE)

    league_shortcuts = set() if entry_type == "team" else {(league_shortcut, season)}
    team_filters = set()
    team_ids = {}

    if entry_type == "team":
        team_name = entry.data.get(CONF_TEAM_NAME, "")
        if team_name:
            key = (league_shortcut, season, team_name)
            team_filters.add(key)
            if CONF_TEAM_ID in entry.data:
                team_ids[key] = entry.data[CONF_TEAM_ID]

    # Reuse or create coordinator for this entry
    coordinator = OpenLigaDBDataUpdateCoordinator(
        hass,
        league_shortcuts=league_shortcuts,
        team_filters=team_filters,
        team_ids=team_ids,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
