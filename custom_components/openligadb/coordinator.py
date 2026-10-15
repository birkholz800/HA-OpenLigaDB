"""Data update coordinator for OpenLigaDB."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from time import monotonic
from typing import Any
from urllib.parse import quote

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import get_client
from .const import (
    API_URL_AVAILABLE_LEAGUES,
    API_URL_AVAILABLE_TEAMS,
    API_URL_CURRENT_GROUP,
    API_URL_SEASON_MATCHES,
    API_URL_TABLE,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    EVENT_OPENLIGADB_GOAL,
    EVENT_OPENLIGADB_MATCH_END,
    EVENT_OPENLIGADB_MATCH_START,
    LIVE_UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


def parse_match_date(date_str: str) -> datetime | None:
    """Parse OpenLigaDB ISO datetime string."""
    if not date_str:
        return None
    try:
        cleaned = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception as err:
        _LOGGER.debug("Error parsing date '%s': %s", date_str, err)
        return None


class OpenLigaDBDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching OpenLigaDB data."""

    def __init__(
        self,
        hass: HomeAssistant,
        league_shortcuts: set[tuple[str, str]],
        team_filters: set[tuple[str, str, str]],  # (origin league, season, name)
        team_ids: dict[tuple[str, str, str], int] | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
        )
        self.client = get_client(hass)
        self.league_shortcuts = league_shortcuts
        self.team_filters = team_filters
        self.team_ids = dict(team_ids or {})
        self._discovery_cache = self.client.discovery_cache
        self._previous_goals: dict[int, int] = {}  # match_id -> count of goals
        self._previous_live_matches: set[int] = set()  # set of live match_ids

    async def _async_fetch_json(self, url: str) -> Any:
        """Fetch JSON data from URL safely."""
        try:
            return await self.client.fetch(url)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Failed to fetch {url}: {err}") from err

    async def _async_fetch_list(self, url: str) -> list[dict[str, Any]]:
        """Require a valid list, distinguishing empty responses from failures."""
        result = await self._async_fetch_json(url)
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise UpdateFailed(f"Invalid list response from {url}")
        return result

    async def _async_discover_team(
        self, key: tuple[str, str, str]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Serialize shared discovery progress, including across entry retries."""
        async with self.client.discovery_lock:
            return await self._async_discover_team_locked(key)

    async def _async_discover_team_locked(
        self, key: tuple[str, str, str]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Probe at most three additional competitions; retain partial progress."""
        cached = self._discovery_cache.get(key)
        if cached and monotonic() - cached[0] < 24 * 60 * 60:
            return cached[1], cached[2]

        origin, season, name = key
        target_id = self.team_ids.get(key)
        if target_id is not None and (type(target_id) is not int or target_id <= 0):
            raise UpdateFailed(f"Invalid configured team ID for {name}")
        origin_teams = await self._async_fetch_list(
            API_URL_AVAILABLE_TEAMS.format(league_shortcut=quote(origin, safe=""), season=quote(season, safe=""))
        )
        if any(type(team.get("teamId")) is not int for team in origin_teams):
            raise UpdateFailed(f"Invalid team IDs in {origin}/{season}")
        if target_id is None:
            candidates = {
                team["teamId"] for team in origin_teams
                if name.casefold() in (
                    str(team.get("teamName") or "").casefold(),
                    str(team.get("shortName") or "").casefold(),
                )
            }
            if len(candidates) != 1:
                raise UpdateFailed(f"Cannot uniquely resolve team {name} in {origin}/{season}")
            target_id = candidates.pop()

        target = next((team for team in origin_teams if team["teamId"] == target_id), None)
        if target is None:
            raise UpdateFailed(f"Team ID {target_id} not found in {origin}/{season}")
        self.team_ids[key] = target_id
        progress = self.client.discovery_progress.get(key)
        if progress is None:
            known = {c["league_shortcut"]: c for c in cached[2]} if cached else {}
            known.setdefault(origin, {
                "league_shortcut": origin, "league_name": origin, "season": season,
            })
            progress = {"known": known, "pending": None}
            self.client.discovery_progress[key] = progress
        try:
            if progress["pending"] is None:
                leagues = await self._async_fetch_list(API_URL_AVAILABLE_LEAGUES)
                candidates = {}
                for league in leagues:
                    if str(league.get("leagueSeason")) != season:
                        continue
                    shortcut = league.get("leagueShortcut")
                    if not isinstance(shortcut, str) or not shortcut:
                        raise UpdateFailed("Invalid league shortcut in discovery response")
                    candidates[shortcut] = {
                        "league_shortcut": shortcut,
                        "league_name": league.get("leagueName") or shortcut,
                        "season": season,
                    }
                if origin in candidates:
                    progress["known"][origin] = candidates.pop(origin)
                progress["pending"] = list(candidates.values())
            for _ in range(min(3, len(progress["pending"]))):
                competition = progress["pending"][0]
                shortcut = competition["league_shortcut"]
                try:
                    teams = await self._async_fetch_list(API_URL_AVAILABLE_TEAMS.format(
                        league_shortcut=quote(shortcut, safe=""), season=quote(season, safe="")
                    ))
                    if any(type(team.get("teamId")) is not int for team in teams):
                        raise UpdateFailed("Invalid team ID in discovery response")
                except UpdateFailed:
                    # A broken competition must not starve the rest of the queue.
                    progress["pending"].append(progress["pending"].pop(0))
                    raise
                if any(team["teamId"] == target_id for team in teams):
                    progress["known"][shortcut] = competition
                # Retain previously known competitions even if membership disappears.
                progress["pending"].pop(0)
        except UpdateFailed as err:
            _LOGGER.warning("Incomplete competition discovery for %s: %s", name, err)
        participating = list(progress["known"].values())
        if progress["pending"] == []:
            self._discovery_cache[key] = (monotonic(), target, participating)
            del self.client.discovery_progress[key]
        return target, participating

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from OpenLigaDB for leagues and teams."""
        data: dict[str, Any] = {
            "leagues": {},
            "teams": {},
        }

        now = datetime.now(timezone.utc)
        has_active_live_match = False

        # 1. Fetch League Data (Tables & Current Group)
        for league_shortcut, season in self.league_shortcuts:
            table_url = API_URL_TABLE.format(league_shortcut=league_shortcut, season=season)
            group_url = API_URL_CURRENT_GROUP.format(league_shortcut=league_shortcut)

            table_data = await self._async_fetch_list(table_url)
            group_data = await self._async_fetch_json(group_url)
            if not isinstance(group_data, dict):
                raise UpdateFailed(f"Invalid current group response from {group_url}")

            processed_table = []
            if isinstance(table_data, list):
                for item in table_data:
                    if not isinstance(item, dict):
                        continue
                    processed_table.append(
                        {
                            "rank": len(processed_table) + 1,
                            "team_id": item.get("teamInfoId"),
                            "team_name": item.get("teamName") or "",
                            "short_name": item.get("shortName") or item.get("teamName") or "",
                            "team_icon_url": item.get("teamIconUrl") or "",
                            "points": item.get("points") or 0,
                            "matches": item.get("matches") or 0,
                            "won": item.get("won") or 0,
                            "draw": item.get("draw") or 0,
                            "lost": item.get("lost") or 0,
                            "goals": item.get("goals") or 0,
                            "opponent_goals": item.get("opponentGoals") or 0,
                            "goal_diff": item.get("goalDiff") or 0,
                        }
                    )

            data["leagues"][(league_shortcut, season)] = {
                "league_shortcut": league_shortcut,
                "season": season,
                "current_group": group_data if isinstance(group_data, dict) else {},
                "table": processed_table,
            }

        # 2. Fetch Matches for Teams & Process Matches
        for league_shortcut, season, team_identifier in self.team_filters:
            key = (league_shortcut, season, team_identifier)
            target, competitions = await self._async_discover_team(key)
            target_team_id = target["teamId"]
            target_team_name = target.get("teamName") or team_identifier
            target_team_icon = target.get("teamIconUrl")
            team_matches = []
            seen_match_ids = set()
            for competition in competitions:
                matches = await self._async_fetch_list(API_URL_SEASON_MATCHES.format(
                    league_shortcut=quote(competition["league_shortcut"], safe=""),
                    season=quote(season, safe=""),
                ))
                for match in matches:
                    teams = [match.get("team1"), match.get("team2")]
                    if not any(
                        isinstance(team, dict)
                        and type(team.get("teamId")) is int
                        and team["teamId"] == target_team_id
                        for team in teams
                    ):
                        continue
                    match_id = match.get("matchID")
                    if type(match_id) is not int:
                        raise UpdateFailed("Missing or invalid match ID in match response")
                    if match_id in seen_match_ids:
                        continue
                    seen_match_ids.add(match_id)
                    team_matches.append({**match, **competition})

            # Categorize matches: finished (last), current (live), next (upcoming)
            last_match = None
            current_match = None
            next_match = None
            upcoming_matches = []

            sorted_matches = sorted(
                team_matches,
                key=lambda x: (parse_match_date(x.get("matchDateTimeUTC"))
                                               or parse_match_date(x.get("matchDateTime")))
                or datetime.min.replace(tzinfo=timezone.utc),
            )

            for m in sorted_matches:
                if not isinstance(m, dict):
                    continue

                m_date = (parse_match_date(m.get("matchDateTimeUTC"))
                          or parse_match_date(m.get("matchDateTime")))
                is_finished = bool(m.get("matchIsFinished"))

                # Determine live status
                is_live = False
                if m_date and not is_finished:
                    time_diff = (now - m_date).total_seconds()
                    if -900 <= time_diff <= 9000:
                        is_live = True
                        has_active_live_match = True

                t1 = m.get("team1") or {}
                t2 = m.get("team2") or {}
                if not isinstance(t1, dict):
                    t1 = {}
                if not isinstance(t2, dict):
                    t2 = {}

                t1_id = t1.get("teamId")
                t1_name = t1.get("teamName") or ""
                t2_name = t2.get("teamName") or ""

                group = m.get("group") or {}
                if not isinstance(group, dict):
                    group = {}

                location = m.get("location") or {}
                if not isinstance(location, dict):
                    location = {}

                is_home = type(t1_id) is int and t1_id == target_team_id
                opponent = t2 if is_home else t1

                # Extract latest score
                match_results = m.get("matchResults") or []
                if not isinstance(match_results, list):
                    match_results = []

                valid_results = [r for r in match_results if isinstance(r, dict)]
                score_t1 = 0
                score_t2 = 0
                if valid_results:
                    final_res = max(valid_results, key=lambda r: r.get("resultOrderID") or 0)
                    score_t1 = final_res.get("pointsTeam1") or 0
                    score_t2 = final_res.get("pointsTeam2") or 0

                goals = m.get("goals") or []
                if not isinstance(goals, list):
                    goals = []

                # Separate home & away goals
                home_goals = []
                away_goals = []
                prev_s1 = 0
                prev_s2 = 0

                valid_goals = [g for g in goals if isinstance(g, dict)]
                sorted_goals = sorted(valid_goals, key=lambda g: g.get("matchMinute") or 0)

                for g in sorted_goals:
                    s1 = g.get("scoreTeam1") if g.get("scoreTeam1") is not None else prev_s1
                    s2 = g.get("scoreTeam2") if g.get("scoreTeam2") is not None else prev_s2
                    player_name = (g.get("goalGetterName") or g.get("goalGetter") or "").strip()

                    g_info = {
                        "minute": g.get("matchMinute"),
                        "player": player_name,
                        "score": f"{s1}:{s2}",
                        "is_penalty": bool(g.get("isPenalty")),
                        "is_own_goal": bool(g.get("isOwnGoal")),
                    }

                    if s1 > prev_s1:
                        home_goals.append(g_info)
                        prev_s1 = s1
                    elif s2 > prev_s2:
                        away_goals.append(g_info)
                        prev_s2 = s2
                    else:
                        home_goals.append(g_info)

                # Process Cards
                cards = m.get("cards") or m.get("cardEvents") or []
                if not isinstance(cards, list):
                    cards = []

                home_cards = []
                away_cards = []
                for c in cards:
                    if isinstance(c, dict):
                        player_name = (c.get("playerName") or c.get("player") or "").strip()
                        c_info = {
                            "minute": c.get("matchMinute"),
                            "player": player_name,
                            "card_type": str(c.get("cardType", "yellow")).lower(),
                        }
                        if c.get("teamId") == t1_id:
                            home_cards.append(c_info)
                        else:
                            away_cards.append(c_info)

                stadium = location.get("locationStadium") or ""
                city = location.get("locationCity") or ""
                loc_str = ", ".join(filter(None, [stadium, city]))

                match_dict = {
                    "match_id": m.get("matchID"),
                    "date": m_date.isoformat() if m_date else None,
                    "league_shortcut": m["league_shortcut"],
                    "league_name": m["league_name"],
                    "season": m["season"],
                    "group": group.get("groupName") or "",
                    "is_home": is_home,
                    "home_team_name": t1_name,
                    "home_team_icon": t1.get("teamIconUrl") or "",
                    "away_team_name": t2_name,
                    "away_team_icon": t2.get("teamIconUrl") or "",
                    "opponent_name": opponent.get("teamName") or "",
                    "opponent_short_name": opponent.get("shortName") or "",
                    "opponent_icon": opponent.get("teamIconUrl") or "",
                    "score_home": score_t1,
                    "score_away": score_t2,
                    "score_team": score_t1 if is_home else score_t2,
                    "score_opponent": score_t2 if is_home else score_t1,
                    "score_str": f"{score_t1} - {score_t2}" if (valid_results or is_finished or is_live) else "-:-",
                    "is_finished": is_finished,
                    "is_live": is_live,
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "home_cards": home_cards,
                    "away_cards": away_cards,
                    "location": loc_str,
                }

                if not is_finished and not is_live and m_date and m_date > now:
                    if len(upcoming_matches) < 20:
                        upcoming_matches.append(match_dict)

                # Check for Goal & Match Start/End Events
                match_id = m.get("matchID")
                if is_live:
                    current_match = match_dict
                    if match_id and match_id not in self._previous_live_matches:
                        self._previous_live_matches.add(match_id)
                        self.hass.bus.async_fire(
                            EVENT_OPENLIGADB_MATCH_START,
                            {
                                "team": target_team_name,
                                "opponent": opponent.get("teamName") or "",
                                "match_id": match_id,
                            },
                        )

                    # Check for new goals
                    all_goals = home_goals + away_goals
                    goals_count = len(all_goals)
                    prev_goals = self._previous_goals.get(match_id, 0)
                    if goals_count > prev_goals and all_goals:
                        latest_goal = all_goals[-1]
                        self._previous_goals[match_id] = goals_count
                        self.hass.bus.async_fire(
                            EVENT_OPENLIGADB_GOAL,
                            {
                                "team": target_team_name,
                                "opponent": opponent.get("teamName") or "",
                                "score": match_dict["score_str"],
                                "goal_getter": latest_goal.get("player") or "Unknown Player",
                                "minute": latest_goal.get("minute"),
                                "match_id": match_id,
                            },
                        )
                elif is_finished:
                    if match_id and match_id in self._previous_live_matches:
                        self._previous_live_matches.remove(match_id)
                        self.hass.bus.async_fire(
                            EVENT_OPENLIGADB_MATCH_END,
                            {
                                "team": target_team_name,
                                "opponent": opponent.get("teamName") or "",
                                "score": match_dict["score_str"],
                                "match_id": match_id,
                            },
                        )
                    if m_date and m_date <= now:
                        last_match = match_dict
                elif m_date and m_date > now and next_match is None:
                    next_match = match_dict

            data["teams"][(league_shortcut, season, team_identifier)] = {
                "team_name": target_team_name,
                "team_id": target_team_id,
                "team_icon": target_team_icon,
                "league_shortcut": league_shortcut,
                "season": season,
                "last_match": last_match,
                "current_match": current_match,
                "next_match": next_match,
                "competitions": competitions,
                "discovery_complete": key not in self.client.discovery_progress,
                "upcoming_matches": upcoming_matches,
            }

        # Dynamically adjust polling interval based on live matches
        if has_active_live_match:
            _LOGGER.debug("Live match detected. Switching update interval to 60s.")
            self.update_interval = timedelta(seconds=LIVE_UPDATE_INTERVAL_SECONDS)
        else:
            self.update_interval = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)

        return data
