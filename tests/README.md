# Offline unit tests

Run from the repository root with Python 3.12 or newer, without installing dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*_offline.py' -v
```

The tests load the real coordinator, config flow and constants under a synthetic
package, avoiding the integration's `__init__.py`. Minimal Home Assistant,
aiohttp and voluptuous modules are injected only for each test and `sys.modules`
is restored afterwards. HTTP responses are local fixtures; unconfigured HTTP
calls and socket connections fail. No API access or package installation is needed.
The clock is fixed to January 2, 2031, to distinguish the calendar-year default
from a previous-year football-season default. Bytecode writing is disabled by
the command above so production directories are not modified.

Coverage includes cross-competition numeric team identity (including renamed
teams and same-name non-matches), legacy name resolution, chronological next/last
and upcoming matches, deduplication, upcoming limit, live exclusion, discovery
cache expiry and atomic failure/retry, malformed responses, dynamic/historical
league options, editable season validation, team IDs and encoded URLs.

Scope: these are unit tests, not Home Assistant integration tests. Stubs capture
form schemas but do not implement voluptuous validation, HA lifecycle, duplicate
entry handling or real aiohttp transport behavior. Expected production warnings
are visible for deliberately injected errors.
