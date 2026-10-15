"""Sensor platform for OpenLigaDB."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENTRY_TYPE,
    CONF_LEAGUE_SHORTCUT,
    CONF_SEASON,
    CONF_TEAM_NAME,
    DOMAIN,
)
from .coordinator import OpenLigaDBDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenLigaDB sensors based on a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: OpenLigaDBDataUpdateCoordinator = entry_data["coordinator"]

    entry_type = entry.data.get(CONF_ENTRY_TYPE)
    league_shortcut = entry.data.get(CONF_LEAGUE_SHORTCUT, "bl1")
    season = str(entry.data.get(CONF_SEASON, dt_util.now().year))

    entities: list[SensorEntity] = []

    if entry_type == "team":
        team_name = entry.data.get(CONF_TEAM_NAME, "")
        entities.append(
            OpenLigaDBTeamSensor(coordinator, entry, league_shortcut, season, team_name)
        )
    elif entry_type == "league":
        entities.append(
            OpenLigaDBLeagueSensor(coordinator, entry, league_shortcut, season)
        )

    async_add_entities(entities)


class OpenLigaDBTeamSensor(CoordinatorEntity[OpenLigaDBDataUpdateCoordinator], SensorEntity):
    """Representation of an OpenLigaDB Team Sensor."""

    _attr_icon = "mdi:soccer"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpenLigaDBDataUpdateCoordinator,
        entry: ConfigEntry,
        league_shortcut: str,
        season: str,
        team_identifier: str,
    ) -> None:
        """Initialize the team sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self.league_shortcut = league_shortcut
        self.season = season
        self.team_identifier = team_identifier
        self._attr_unique_id = f"openligadb_team_{league_shortcut}_{season}_{team_identifier}".lower().replace(" ", "_")

    @property
    def team_data(self) -> dict[str, Any]:
        """Return raw coordinator data for this team."""
        if not self.coordinator.data or "teams" not in self.coordinator.data:
            return {}
        key = (self.league_shortcut, self.season, self.team_identifier)
        return self.coordinator.data["teams"].get(key, {})

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        team_name = self.team_data.get("team_name", self.team_identifier)
        return f"OpenLigaDB {team_name}"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        t_data = self.team_data
        cur = t_data.get("current_match")
        nxt = t_data.get("next_match")
        lst = t_data.get("last_match")

        if cur and cur.get("is_live"):
            return f"Live: {cur.get('score_str')}"
        if nxt:
            opp = nxt.get("opponent_name", "Unknown")
            location_type = "vs" if nxt.get("is_home") else "@"
            return f"Upcoming ({location_type} {opp})"
        if lst:
            return f"Finished: {lst.get('score_str')}"

        return "No Match"

    @property
    def entity_picture(self) -> str | None:
        """Return team logo URL as HA entity picture."""
        return self.team_data.get("team_icon")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        t_data = self.team_data
        return {
            "team_name": t_data.get("team_name", self.team_identifier),
            "team_id": t_data.get("team_id"),
            "league_shortcut": self.league_shortcut,
            "season": self.season,
            "last_match": t_data.get("last_match"),
            "current_match": t_data.get("current_match"),
            "next_match": t_data.get("next_match"),

        }


class OpenLigaDBLeagueSensor(CoordinatorEntity[OpenLigaDBDataUpdateCoordinator], SensorEntity):
    """Representation of an OpenLigaDB League Sensor."""

    _attr_icon = "mdi:trophy-outline"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpenLigaDBDataUpdateCoordinator,
        entry: ConfigEntry,
        league_shortcut: str,
        season: str,
    ) -> None:
        """Initialize the league sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self.league_shortcut = league_shortcut
        self.season = season
        self._attr_unique_id = f"openligadb_league_{league_shortcut}_{season}".lower()

    @property
    def league_data(self) -> dict[str, Any]:
        """Return raw coordinator data for this league."""
        if not self.coordinator.data or "leagues" not in self.coordinator.data:
            return {}
        key = (self.league_shortcut, self.season)
        return self.coordinator.data["leagues"].get(key, {})

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return f"OpenLigaDB {self.league_shortcut.upper()} Standings {self.season}"

    @property
    def native_value(self) -> str:
        """Return state of the league (current group or season)."""
        l_data = self.league_data
        group = l_data.get("current_group", {})
        if isinstance(group, dict) and group.get("groupName"):
            return group.get("groupName")
        return f"Season {self.season}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return league table and standings as attributes."""
        l_data = self.league_data
        return {
            "league_shortcut": self.league_shortcut,
            "season": self.season,
            "current_group": l_data.get("current_group"),
            "table": l_data.get("table", []),
        }
