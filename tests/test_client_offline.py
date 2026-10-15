import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch

from offline_support import OfflineEnvironment, Response, UpdateFailed
from test_coordinator_offline import KEY, TEAM, match

BASE = "https://api.openligadb.de"


class SharedClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = OfflineEnvironment().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.env.hass.session.get.side_effect = None
        self.c = self.coordinator()

    def coordinator(self):
        return self.env.coordinator.OpenLigaDBDataUpdateCoordinator(
            self.env.hass, set(), {KEY}, {KEY: 7})

    async def test_429_cooldown_survives_recreation_and_config_flow(self):
        session = self.env.hass.session
        session.get.return_value = Response(status=429, headers={"Retry-After": "120"})
        with patch.object(self.env.client, "monotonic", return_value=100):
            with self.assertRaisesRegex(UpdateFailed, "429"):
                await self.c._async_fetch_list(BASE + "/getavailableteams/bl1/2031")
        other = self.coordinator()
        self.assertIs(other.client, self.c.client)
        flow = self.env.config_flow.OpenLigaDBConfigFlow()
        flow.hass = self.env.hass
        with patch.object(self.env.client, "monotonic", return_value=219):
            with self.assertRaisesRegex(UpdateFailed, "cooldown"):
                await other._async_fetch_list(BASE + "/getavailableteams/cup/2031")
            form = await flow.async_step_league()
            self.assertEqual(form["errors"], {"base": "cannot_connect"})
        self.assertEqual(session.get.call_count, 1)
        session.get.return_value = Response([TEAM])
        with patch.object(self.env.client, "monotonic", return_value=220):
            self.assertEqual(await other._async_fetch_list(BASE + "/getavailableteams/bl1/2031"), [TEAM])
        self.assertEqual(session.get.call_count, 2)

    async def test_retry_after_http_date_and_invalid_fallback(self):
        for header, minimum in [(format_datetime(datetime.now(timezone.utc) + timedelta(seconds=180)), 178),
                                ("invalid", 60), ("-1", 60), ("0", 60)]:
            self.c.client.cooldown_until = 0
            self.env.hass.session.get.return_value = Response(status=429, headers={"Retry-After": header})
            with patch.object(self.env.client, "monotonic", return_value=100):
                with self.assertRaises(UpdateFailed):
                    await self.c._async_fetch_list(BASE + "/getavailableleagues")
            self.assertGreaterEqual(self.c.client.cooldown_until, 100 + minimum)

    async def test_success_reused_after_partial_failure_and_during_cooldown(self):
        session = self.env.hass.session
        session.get.side_effect = [Response([TEAM]), Response(status=429)]
        origin = BASE + "/getavailableteams/bl1/2031"
        self.assertEqual(await self.c._async_fetch_list(origin), [TEAM])
        with self.assertRaises(UpdateFailed):
            await self.c._async_fetch_list(BASE + "/getavailableteams/cup/2031")
        other = self.coordinator()
        result = await other._async_fetch_list(origin)
        result.clear()
        flow = self.env.config_flow.OpenLigaDBConfigFlow()
        flow.hass = self.env.hass
        self.assertEqual(await flow._async_fetch_list(origin), [TEAM])
        self.assertEqual(session.get.call_count, 2)

    async def test_concurrent_requests_coalesce_and_distinct_requests_are_paced(self):
        self.env.hass.session.get.return_value = Response([TEAM])
        with patch.object(self.env.client, "REQUEST_INTERVAL", 1), \
             patch.object(self.env.client, "monotonic", return_value=100), \
             patch.object(self.env.client.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await asyncio.gather(*(self.coordinator()._async_fetch_list(
                BASE + "/getavailableteams/bl1/2031") for _ in range(5)))
            self.assertEqual(self.env.hass.session.get.call_count, 1)
            await self.c._async_fetch_list(BASE + "/getavailableteams/cup/2031")
            sleep.assert_awaited_once_with(1)

    async def test_team_endpoint_shared_cache_and_recreation(self):
        routes = {
            "/getavailableteams/bl1/2031": [TEAM],
            "/getmatchesbyteamid/7/26/26": [match(1, 24), match(2, 12, competition="ucl")],
        }
        self.env.hass.session.get.side_effect = lambda url, **kw: Response(routes[url.removeprefix(BASE)])
        with patch.object(self.env.client, "monotonic", return_value=100):
            first = await self.c._async_update_data()
            second = await self.coordinator()._async_update_data()
        self.assertEqual(first, second)
        self.assertEqual(first["teams"][KEY]["next_match"]["league_shortcut"], "ucl")
        self.assertEqual(self.env.hass.session.get.call_count, 2)
        with patch.object(self.env.client, "monotonic", return_value=130):
            await self.c._async_update_data()
        self.assertEqual(self.env.hass.session.get.call_count, 3)
