"""Config flow for OpenLigaDB integration."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    API_URL_AVAILABLE_LEAGUES,
    API_URL_AVAILABLE_TEAMS,
    CONF_ENTRY_TYPE,
    CONF_ENTRY_TYPE_LEAGUE,
    CONF_ENTRY_TYPE_TEAM,
    CONF_LEAGUE_SHORTCUT,
    CONF_SEASON,
    CONF_TEAM_ID,
    CONF_TEAM_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class OpenLigaDBConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OpenLigaDB."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow state."""
        self._league_shortcut = "bl1"
        self._season = str(dt_util.now().year)
        self._leagues: dict[str, dict[str, str]] = {}
        self._teams: dict[str, str] = {}

    async def _async_fetch_list(self, url: str) -> list[dict[str, Any]]:
        """Fetch an API list without silently substituting static data."""
        session = async_get_clientsession(self.hass)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            data = await resp.json()
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("Expected a list of objects from OpenLigaDB")
        return data

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Select whether to add a team or a league table."""
        if user_input is not None:
            if user_input[CONF_ENTRY_TYPE] == CONF_ENTRY_TYPE_LEAGUE:
                return await self.async_step_league()
            return await self.async_step_team_league_select()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ENTRY_TYPE, default=CONF_ENTRY_TYPE_TEAM): vol.In({
                    CONF_ENTRY_TYPE_TEAM: "Team Sensor (Next/Last Match, Status)",
                    CONF_ENTRY_TYPE_LEAGUE: "League Sensor (Standings & Matchday)",
                }),
            }),
        )

    async def async_step_league(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure a league table sensor."""
        return await self._async_select_league("league", user_input)

    async def async_step_team_league_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Select league and editable season before choosing the team."""
        return await self._async_select_league("team_league_select", user_input)

    async def _async_select_league(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> config_entries.FlowResult:
        """Load all leagues and validate the requested league/season pair."""
        errors: dict[str, str] = {}
        try:
            if not self._leagues:
                leagues: dict[str, dict[str, str]] = {}
                for league in await self._async_fetch_list(API_URL_AVAILABLE_LEAGUES):
                    shortcut = league.get("leagueShortcut")
                    name = league.get("leagueName")
                    season = league.get("leagueSeason")
                    if not isinstance(shortcut, str) or not shortcut.strip():
                        raise ValueError("Missing league shortcut")
                    if not isinstance(name, str) or not isinstance(season, (str, int)):
                        raise ValueError("Invalid league name or season")
                    leagues.setdefault(shortcut, {})[str(season)] = name
                if not leagues:
                    raise ValueError("No leagues returned")
                self._leagues = leagues

            if user_input is not None and CONF_LEAGUE_SHORTCUT in user_input:
                self._league_shortcut = user_input[CONF_LEAGUE_SHORTCUT]
                self._season = str(user_input[CONF_SEASON]).strip()
                if self._season not in self._leagues.get(self._league_shortcut, {}):
                    errors[CONF_SEASON] = "invalid_season"
                elif step_id == "league":
                    shortcut = self._league_shortcut
                    season = self._season
                    await self.async_set_unique_id(f"openligadb_league_{shortcut.lower()}_{season}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"League: {shortcut.upper()} ({season})",
                        data={
                            CONF_ENTRY_TYPE: CONF_ENTRY_TYPE_LEAGUE,
                            CONF_LEAGUE_SHORTCUT: shortcut,
                            CONF_SEASON: season,
                        },
                    )
                else:
                    self._teams = {}
                    url = API_URL_AVAILABLE_TEAMS.format(
                        league_shortcut=quote(self._league_shortcut, safe=""),
                        season=quote(self._season, safe=""),
                    )
                    teams = {}
                    for team in await self._async_fetch_list(url):
                        team_id = team.get("teamId")
                        name = team.get("teamName")
                        if type(team_id) is not int or team_id <= 0 or not isinstance(name, str) or not name.strip():
                            raise ValueError("Invalid team ID or name")
                        teams[str(team_id)] = name.strip()
                    if teams:
                        self._teams = dict(sorted(teams.items(), key=lambda item: item[1].casefold()))
                        return await self.async_step_team_select()
                    errors["base"] = "no_teams"
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Could not connect to OpenLigaDB: %s", err)
            errors["base"] = "cannot_connect"
        except ValueError as err:
            _LOGGER.warning("Invalid OpenLigaDB response: %s", err)
            errors["base"] = "invalid_response"

        fields = {}
        if self._leagues:
            # Keep every shortcut, including historical and non-football leagues.
            options = {
                shortcut: f"{seasons.get(self._season, seasons[max(seasons)])} ({shortcut})"
                for shortcut, seasons in self._leagues.items()
            }
            options = dict(sorted(options.items(), key=lambda item: item[1].casefold()))
            default = self._league_shortcut if self._league_shortcut in options else next(iter(options))
            fields = {
                vol.Required(CONF_LEAGUE_SHORTCUT, default=default): vol.In(options),
                vol.Required(CONF_SEASON, default=self._season): str,
            }
        return self.async_show_form(
            step_id=step_id, data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_team_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Select a real API team ID while retaining name-based compatibility."""
        if not self._teams:
            return await self.async_step_team_league_select()
        errors: dict[str, str] = {}
        if user_input is not None:
            team_id = str(user_input.get(CONF_TEAM_ID, ""))
            if team_id not in self._teams:
                errors[CONF_TEAM_ID] = "invalid_team"
            else:
                team_name = self._teams[team_id]
                unique_id = f"openligadb_team_{self._league_shortcut}_{self._season}_{team_name}".lower().replace(" ", "_")
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Team: {team_name} ({self._league_shortcut.upper()})",
                    data={
                        CONF_ENTRY_TYPE: CONF_ENTRY_TYPE_TEAM,
                        CONF_LEAGUE_SHORTCUT: self._league_shortcut,
                        CONF_SEASON: self._season,
                        CONF_TEAM_ID: int(team_id),
                        CONF_TEAM_NAME: team_name,
                    },
                )
        return self.async_show_form(
            step_id="team_select",
            data_schema=vol.Schema({
                vol.Required(CONF_TEAM_ID): vol.In({
                    team_id: f"{name} ({team_id})" for team_id, name in self._teams.items()
                }),
            }),
            errors=errors,
        )
