import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from offline_support import ClientError, NOW, OfflineEnvironment, Response, UpdateFailed

KEY = ("bl1", "2031", "Original Name")
TEAM = {"teamId": 7, "teamName": "Original Name", "shortName": "ON"}


def league(shortcut, season=2031):
    return {"leagueShortcut": shortcut, "leagueSeason": season, "leagueName": shortcut.upper()}


def match(mid, hours, team_id=7, away=False, finished=False):
    target = {"teamId": team_id, "teamName": "Renamed Team"}
    other = {"teamId": 99, "teamName": "Opponent"}
    return {"matchID": mid, "matchDateTimeUTC": (NOW + timedelta(hours=hours)).isoformat(),
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
            "/getavailableleagues": [league("bl1"), league("cup"), league("other"), league("old", 2030), league("cup")],
            "/getavailableteams/cup/2031": [{"teamId": 7, "teamName": "Renamed Team"}],
            "/getavailableteams/other/2031": [{"teamId": 8, "teamName": "Original Name"}],
        }

        async def fetch(url):
            value = self.routes[url.removeprefix("https://api.openligadb.de")]
            if isinstance(value, Exception):
                raise value
            return value

        self.c._async_fetch_list = AsyncMock(side_effect=fetch)

    async def test_discovery_matches_ids_across_competitions_not_names(self):
        target, competitions = await self.c._async_discover_team(KEY)
        self.assertEqual(target, TEAM)
        self.assertEqual([c["league_shortcut"] for c in competitions], ["bl1", "cup"])
        self.assertEqual(self.c._async_fetch_list.await_count, 4)

    async def test_legacy_casefold_short_name_and_ambiguous_name(self):
        self.c.team_ids.clear()
        key = ("bl1", "2031", "on")
        await self.c._async_discover_team(key)
        self.assertEqual(self.c.team_ids[key], 7)
        self.routes["/getavailableteams/bl1/2031"] = [TEAM, {"teamId": 8, "shortName": "ON"}]
        self.c.team_ids.clear()
        self.c._discovery_cache.clear()
        with self.assertRaisesRegex(UpdateFailed, "uniquely resolve"):
            await self.c._async_discover_team(key)
        self.assertEqual(self.c._discovery_cache, {})

    async def test_discovery_cache_hit_and_exact_24_hour_expiry(self):
        with patch.object(self.env.coordinator, "monotonic", return_value=100):
            first = await self.c._async_discover_team(KEY)
        self.c._async_fetch_list.reset_mock()
        with patch.object(self.env.coordinator, "monotonic", return_value=86499):
            self.assertEqual(await self.c._async_discover_team(KEY), first)
        self.c._async_fetch_list.assert_not_awaited()
        with patch.object(self.env.coordinator, "monotonic", return_value=86500):
            await self.c._async_discover_team(KEY)
        self.assertEqual(self.c._async_fetch_list.await_count, 4)

    async def test_discovery_failure_is_atomic_and_retry_succeeds(self):
        self.c.team_ids.clear()
        self.routes["/getavailableteams/cup/2031"] = UpdateFailed("offline failure")
        with self.assertRaisesRegex(UpdateFailed, "cup"):
            await self.c._async_discover_team(KEY)
        self.assertEqual(self.c._discovery_cache, {})
        self.assertEqual(self.c.team_ids, {})
        self.routes["/getavailableteams/cup/2031"] = [TEAM]
        self.assertEqual(len((await self.c._async_discover_team(KEY))[1]), 2)

    async def test_invalid_configured_ids_rejected_before_requests(self):
        for invalid in (True, "7", 0, -1):
            with self.subTest(invalid=invalid):
                self.c.team_ids[KEY] = invalid
                with self.assertRaisesRegex(UpdateFailed, "Invalid configured"):
                    await self.c._async_discover_team(KEY)
        self.c._async_fetch_list.assert_not_awaited()

    async def test_chronological_next_upcoming_dedup_and_id_filter(self):
        self.routes["/getmatchdata/bl1/2031"] = [match(30, 72), match(10, 24), match(2, -24, finished=True),
                                                      match(90, 1, team_id=8), match(91, 2, team_id="7")]
        self.routes["/getmatchdata/cup/2031"] = [match(20, 48), match(10, 24), match(5, 5, away=True),
                                                      match(1, -48, finished=True)]
        result = (await self.c._async_update_data())["teams"][KEY]
        self.assertEqual([m["match_id"] for m in result["upcoming_matches"]], [5, 10, 20, 30])
        self.assertEqual(result["next_match"]["match_id"], 5)
        self.assertEqual(result["next_match"]["league_shortcut"], "cup")
        self.assertFalse(result["next_match"]["is_home"])
        self.assertEqual(result["next_match"]["opponent_name"], "Opponent")
        self.assertEqual(result["last_match"]["match_id"], 2)
        self.assertIsNone(result["current_match"])
        self.env.hass.bus.async_fire.assert_not_called()

    async def test_upcoming_limit_and_live_exclusion(self):
        self.routes["/getmatchdata/bl1/2031"] = [match(i, i) for i in range(30, 0, -1)] + [match(100, 0)]
        self.routes["/getmatchdata/cup/2031"] = []
        result = (await self.c._async_update_data())["teams"][KEY]
        self.assertEqual([m["match_id"] for m in result["upcoming_matches"]], list(range(1, 21)))
        self.assertEqual(result["current_match"]["match_id"], 100)
        self.assertEqual(result["next_match"]["match_id"], 1)
        self.assertEqual(self.c.update_interval, timedelta(seconds=60))

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
