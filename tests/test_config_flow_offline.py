import unittest
from unittest.mock import AsyncMock

from offline_support import ClientError, OfflineEnvironment, Response, field

LEAGUES = [
    {"leagueShortcut": "hockey/custom", "leagueName": "Ice League", "leagueSeason": 2031},
    {"leagueShortcut": "historic", "leagueName": "Historic League", "leagueSeason": "1999"},
    {"leagueShortcut": "hockey/custom", "leagueName": "Old Ice League", "leagueSeason": 2030},
]


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = OfflineEnvironment().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.flow = self.env.config_flow.OpenLigaDBConfigFlow()
        self.flow.hass = self.env.hass

    async def test_current_calendar_year_default_in_both_flows(self):
        # January deliberately distinguishes calendar year from football season.
        for step in ("league", "team_league_select"):
            with self.subTest(step=step):
                flow = self.env.config_flow.OpenLigaDBConfigFlow()
                flow._async_fetch_list = AsyncMock(return_value=LEAGUES)
                form = await getattr(flow, f"async_step_{step}")()
                self.assertEqual(field(form, "season")[0].default(), "2031")
                self.assertEqual(form["errors"], {})

    async def test_dynamic_historical_leagues_and_cached_catalog(self):
        self.flow._async_fetch_list = AsyncMock(return_value=LEAGUES)
        form = await self.flow.async_step_league()
        options = field(form, "league_shortcut")[1].container
        self.assertEqual(set(options), {"hockey/custom", "historic"})
        self.assertEqual(options["hockey/custom"], "Ice League (hockey/custom)")
        invalid = await self.flow.async_step_league({"league_shortcut": "historic", "season": "2031"})
        self.assertEqual(invalid["errors"], {"season": "invalid_season"})
        entry = await self.flow.async_step_league({"league_shortcut": "historic", "season": " 1999 "})
        self.assertEqual(entry["type"], "create_entry")
        self.assertEqual(entry["data"], {"entry_type": "league", "league_shortcut": "historic", "season": "1999"})
        self.assertEqual(self.flow.unique_id, "openligadb_league_historic_1999")
        self.flow._async_fetch_list.assert_awaited_once()

    async def test_team_selection_url_encoding_sorting_and_real_id(self):
        self.flow._async_fetch_list = AsyncMock(side_effect=[LEAGUES, [
            {"teamId": 42, "teamName": " Zebra "}, {"teamId": 7, "teamName": "alpha"}]])
        form = await self.flow.async_step_team_league_select({"league_shortcut": "hockey/custom", "season": 2031})
        self.assertEqual(form["step_id"], "team_select")
        self.assertEqual(list(field(form, "team_id")[1].container), ["7", "42"])
        self.assertTrue(self.flow._async_fetch_list.await_args.args[0].endswith("/hockey%2Fcustom/2031"))
        invalid = await self.flow.async_step_team_select({"team_id": "999"})
        self.assertEqual(invalid["errors"], {"team_id": "invalid_team"})
        entry = await self.flow.async_step_team_select({"team_id": "42"})
        self.assertEqual(entry["data"], {"entry_type": "team", "league_shortcut": "hockey/custom",
                                         "season": "2031", "team_id": 42, "team_name": "Zebra"})
        self.assertEqual(self.flow.unique_id, "openligadb_team_hockey/custom_2031_zebra")

    async def test_catalog_errors_have_no_static_fallback_and_allow_retry(self):
        for error, expected in [(ClientError("offline"), "cannot_connect"),
                                (TimeoutError(), "cannot_connect"),
                                (ValueError("bad JSON"), "invalid_response")]:
            with self.subTest(error=error):
                self.flow._leagues = {}
                self.flow._async_fetch_list = AsyncMock(side_effect=[error, LEAGUES])
                form = await self.flow.async_step_league()
                self.assertEqual(form["errors"], {"base": expected})
                self.assertEqual(form["data_schema"].schema, {})
                self.assertEqual(self.flow._leagues, {})
                self.assertEqual((await self.flow.async_step_league())["errors"], {})

    async def test_empty_or_invalid_catalog_not_cached(self):
        for payload in ([], [{}], [LEAGUES[0], {"leagueShortcut": " "}]):
            with self.subTest(payload=payload):
                self.flow._async_fetch_list = AsyncMock(return_value=payload)
                form = await self.flow.async_step_league()
                self.assertEqual(form["errors"], {"base": "invalid_response"})
                self.assertEqual(self.flow._leagues, {})

    async def test_empty_and_invalid_teams(self):
        self.flow._leagues = {"hockey/custom": {"2031": "Ice League"}}
        for teams, expected in [([], "no_teams"), ([{"teamId": True, "teamName": "X"}], "invalid_response"),
                                ([{"teamId": "7", "teamName": "X"}], "invalid_response"),
                                ([{"teamId": 7, "teamName": " "}], "invalid_response")]:
            with self.subTest(teams=teams):
                self.flow._teams = {"99": "Stale"}
                self.flow._async_fetch_list = AsyncMock(return_value=teams)
                form = await self.flow.async_step_team_league_select({"league_shortcut": "hockey/custom", "season": "2031"})
                self.assertEqual(form["errors"], {"base": expected})
                self.assertEqual(self.flow._teams, {})

    async def test_real_fetch_boundary_validates_list_and_http_errors(self):
        self.env.hass.session.get.side_effect = None
        for payload in ({}, [None], ["not an object"]):
            with self.subTest(payload=payload):
                self.env.hass.session.get.return_value = Response(payload)
                with self.assertRaises(ValueError):
                    await self.flow._async_fetch_list("offline://test")
        self.env.hass.session.get.return_value = Response(LEAGUES)
        self.assertEqual(await self.flow._async_fetch_list("offline://test"), LEAGUES)
        self.env.hass.session.get.return_value = Response(error=ClientError("HTTP"))
        form = await self.flow.async_step_league()
        self.assertEqual(form["errors"], {"base": "cannot_connect"})
