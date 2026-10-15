import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from offline_support import ClientError, NOW, OfflineEnvironment, Response, UpdateFailed

KEY = ("bl1", "2031", "Original Name")
TEAM = {"teamId": 7, "teamName": "Original Name", "shortName": "ON"}


def league(shortcut, season=2031):
    return {"leagueShortcut": shortcut, "leagueSeason": season, "leagueName": shortcut.upper()}


def match(mid, hours, team_id=7, away=False, finished=False, competition="bl1", season=2031):
    target = {"teamId": team_id, "teamName": "Renamed Team"}
    other = {"teamId": 99, "teamName": "Opponent"}
    return {"leagueShortcut": competition, "leagueName": competition.upper(), "leagueSeason": season, "matchID": mid, "matchDateTimeUTC": (NOW + timedelta(hours=hours)).isoformat(),
            "matchIsFinished": finished, "team1": other if away else target,
            "team2": target if away else other}


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = OfflineEnvironment().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.c = self.env.coordinator.OpenLigaDBDataUpdateCoordinator(
            self.env.hass, set(), {KEY}, {KEY: 7})
        self.routes = {
            "/getavailableteams/bl1/2031": [TEAM],
            "/getmatchesbyteamid/7/26/26": [],
        }

        async def fetch(url):
            value = self.routes[url.removeprefix("https://api.openligadb.de")]
            if isinstance(value, Exception):
                raise value
            return value

        self.c._async_fetch_list = AsyncMock(side_effect=fetch)

    async def test_legacy_casefold_short_name_and_ambiguous_name(self):
        self.c.team_ids.clear()
        key = ("bl1", "2031", "on")
        await self.c._async_resolve_team(key)
        self.assertEqual(self.c.team_ids[key], 7)
        self.routes["/getavailableteams/bl1/2031"] = [TEAM, {"teamId": 8, "shortName": "ON"}]
        self.c.team_ids.clear()
        with self.assertRaisesRegex(UpdateFailed, "uniquely resolve"):
            await self.c._async_resolve_team(key)

    async def test_invalid_configured_ids_rejected_before_requests(self):
        for invalid in (True, "7", 0, -1):
            with self.subTest(invalid=invalid):
                self.c.team_ids[KEY] = invalid
                with self.assertRaisesRegex(UpdateFailed, "Invalid configured"):
                    await self.c._async_resolve_team(KEY)
        self.c._async_fetch_list.assert_not_awaited()

    async def test_cross_competition_and_season_selection(self):
        self.routes["/getmatchesbyteamid/7/26/26"] = [
            match(30, 72), match(10, 24, competition="ucl"),
            match(2, -24, finished=True, competition="ucl", season=2030),
            match(90, 1, team_id=8), match(91, 2, team_id="7"),
            match(5, 5, away=True, competition="dfb"), match(5, 5),
            match(1, -48, finished=True),
        ]
        result = (await self.c._async_update_data())["teams"][KEY]
        self.assertEqual(result["next_match"]["match_id"], 5)
        self.assertEqual(result["next_match"]["league_shortcut"], "dfb")
        self.assertFalse(result["next_match"]["is_home"])
        self.assertEqual(result["last_match"]["match_id"], 2)
        self.assertEqual(result["last_match"]["season"], "2030")
        self.assertIsNone(result["current_match"])
        self.assertNotIn("upcoming_matches", result)
        self.assertNotIn("discovery_complete", result)
        self.assertEqual(self.c._async_fetch_list.await_count, 2)
        self.env.hass.bus.async_fire.assert_not_called()

    async def test_live_and_prematch_selection(self):
        self.routes["/getmatchesbyteamid/7/26/26"] = [match(1, 1), match(100, 0, competition="ucl")]
        result = (await self.c._async_update_data())["teams"][KEY]
        self.assertEqual(result["current_match"]["match_id"], 100)
        self.assertEqual(result["next_match"]["match_id"], 1)
        self.assertEqual(self.c.update_interval, timedelta(seconds=60))
        self.routes["/getmatchesbyteamid/7/26/26"] = [match(2, 0.1)]
        result = (await self.c._async_update_data())["teams"][KEY]
        self.assertIsNone(result["current_match"])
        self.assertEqual(result["next_match"]["match_id"], 2)
        self.assertEqual(self.c.update_interval, timedelta(seconds=60))

    async def test_empty_and_failed_team_endpoint_no_league_fallback(self):
        result = (await self.c._async_update_data())["teams"][KEY]
        for field in ("last_match", "current_match", "next_match"):
            self.assertIsNone(result[field])
        self.routes["/getmatchesbyteamid/7/26/26"] = UpdateFailed("unavailable")
        with self.assertRaises(UpdateFailed):
            await self.c._async_update_data()

    async def test_undated_and_stale_unfinished_not_selected(self):
        undated = match(1, 1)
        undated["matchDateTimeUTC"] = None
        self.routes["/getmatchesbyteamid/7/26/26"] = [undated, match(2, -72)]
        result = (await self.c._async_update_data())["teams"][KEY]
        for field in ("last_match", "current_match", "next_match"):
            self.assertIsNone(result[field])

    async def test_sensor_contract_and_preserved_unique_id(self):
        self.routes["/getmatchesbyteamid/7/26/26"] = [match(1, 12, competition="ucl")]
        self.c.data = await self.c._async_update_data()
        sensor = self.env.sensor.OpenLigaDBTeamSensor(self.c, object(), *KEY)
        self.assertEqual(sensor._attr_unique_id, "openligadb_team_bl1_2031_original_name")
        self.assertEqual(sensor.native_value, "Upcoming (vs Opponent)")
        self.assertEqual(set(sensor.extra_state_attributes), {
            "team_name", "team_id", "league_shortcut", "season",
            "last_match", "current_match", "next_match",
        })
        self.assertEqual(sensor.extra_state_attributes["next_match"]["league_shortcut"], "ucl")

    async def test_fetch_errors_and_malformed_lists(self):
        fetch_list = self.env.coordinator.OpenLigaDBDataUpdateCoordinator._async_fetch_list
        for payload in ({}, [None], [1]):
            with self.subTest(payload=payload):
                self.env.hass.session.get.side_effect = None
                self.env.hass.session.get.return_value = Response(payload)
                with self.assertRaisesRegex(UpdateFailed, "Invalid list"):
                    await fetch_list(self.c, "offline://test")
        for error in (ClientError("HTTP"), TimeoutError("timeout"), ValueError("JSON")):
            with self.subTest(error=error):
                self.env.hass.session.get.return_value = Response(error=error)
                with self.assertRaisesRegex(UpdateFailed, "Failed to fetch"):
                    await self.c._async_fetch_json("offline://test")
        self.env.hass.session.get.return_value = Response([])
        self.assertEqual(await fetch_list(self.c, "offline://test"), [])
