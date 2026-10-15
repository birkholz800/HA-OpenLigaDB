"""HA-instance request pacing and successful-response cache."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic
from urllib.parse import urlsplit

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

REQUEST_INTERVAL = 1.0
DISCOVERY_TTL = 86400


class RateLimited(aiohttp.ClientError):
    """The shared API cooldown is active."""


class OpenLigaDBClient:
    def __init__(self, hass):
        self.session = async_get_clientsession(hass)
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.cooldown_until = 0.0
        self.cache = {}
        self.discovery_cache = {}
        self.discovery_progress = {}
        self.discovery_lock = asyncio.Lock()

    def _check_cooldown(self):
        remaining = self.cooldown_until - monotonic()
        if remaining > 0:
            raise RateLimited(f"OpenLigaDB HTTP 429 cooldown; retry in {remaining:.0f}s")

    async def fetch(self, url):
        # One lock also coalesces concurrent requests for the same URL.
        async with self.lock:
            cached = self.cache.get(url)
            if cached and cached[0] > monotonic():
                return deepcopy(cached[1])
            self._check_cooldown()
            delay = self.next_request - monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._check_cooldown()
            self.next_request = monotonic() + REQUEST_INTERVAL
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 429:
                    retry_after = response.headers.get("Retry-After", "")
                    seconds = 60.0
                    try:
                        seconds = float(int(retry_after))
                    except (ValueError, TypeError, OverflowError):
                        try:
                            date = parsedate_to_datetime(retry_after)
                            seconds = (date - datetime.now(timezone.utc)).total_seconds()
                        except (ValueError, TypeError, OverflowError):
                            pass
                    self.cooldown_until = monotonic() + max(60.0, seconds)
                    self._check_cooldown()
                response.raise_for_status()
                data = await response.json()
            path = urlsplit(url).path
            ttl = 30
            if path.startswith("/getavailableteams/") or path == "/getavailableleagues":
                if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                    raise ValueError("Invalid list response from OpenLigaDB")
                if path.startswith("/getavailableteams/") and any(
                    type(item.get("teamId")) is not int or item["teamId"] <= 0
                    for item in data
                ):
                    raise ValueError("Invalid team ID in OpenLigaDB response")
                if path == "/getavailableleagues" and (not data or any(
                    not isinstance(item.get("leagueShortcut"), str)
                    or not item["leagueShortcut"].strip()
                    or not isinstance(item.get("leagueSeason"), (str, int))
                    or not isinstance(item.get("leagueName"), str)
                    for item in data
                )):
                    raise ValueError("Invalid league catalog from OpenLigaDB")
                ttl = DISCOVERY_TTL
            # Unknown URLs are not cached (also useful for boundary tests).
            if path.startswith("/get"):
                self.cache[url] = (monotonic() + ttl, deepcopy(data))
            return data


def get_client(hass):
    """Keep state across entry unload/retry and share it with config flows."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if "_client" not in domain_data:
        domain_data["_client"] = OpenLigaDBClient(hass)
    return domain_data["_client"]
