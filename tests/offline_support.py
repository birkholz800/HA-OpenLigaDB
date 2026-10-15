"""Minimal dependency boundaries; no HA lifecycle or schema validation emulation."""
import importlib.util
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

NOW = datetime(2031, 1, 2, 12, tzinfo=timezone.utc)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


class UpdateFailed(Exception):
    pass


class ClientError(Exception):
    pass


class Coordinator:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.update_interval = update_interval


class CoordinatorEntity:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator


class ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_show_form(self, **kwargs):
        return dict(type="form", **kwargs)

    def async_create_entry(self, **kwargs):
        return dict(type="create_entry", **kwargs)

    async def async_set_unique_id(self, value):
        self.unique_id = value

    def _abort_if_unique_id_configured(self):
        pass


class Required:
    def __init__(self, schema, default=None):
        self.schema = schema
        self.default = lambda: default


class Schema:
    def __init__(self, schema):
        self.schema = schema


class In:
    def __init__(self, container):
        self.container = container


def field(form, name):
    return next((key, value) for key, value in form["data_schema"].schema.items()
                if key.schema == name)


class OfflineEnvironment:
    """Restore sys.modules after each test, including synthetic production package."""
    def __enter__(self):
        self.stack = ExitStack()
        self.stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("Network forbidden")))
        modules = {}

        def module(name, **attrs):
            result = ModuleType(name)
            result.__dict__.update(attrs)
            modules[name] = result
            return result

        module("aiohttp", ClientError=ClientError, ClientTimeout=lambda **kw: SimpleNamespace(**kw))
        module("voluptuous", Schema=Schema, Required=Required, In=In)
        entries = module("homeassistant.config_entries", ConfigFlow=ConfigFlow, ConfigEntry=object)
        module("homeassistant.components")
        module("homeassistant.components.sensor", SensorEntity=type("SensorEntity", (), {}))
        module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
        module("homeassistant", config_entries=entries)
        module("homeassistant.core", HomeAssistant=object)
        module("homeassistant.helpers")
        module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: hass.session)
        module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=Coordinator, UpdateFailed=UpdateFailed, CoordinatorEntity=CoordinatorEntity)
        dt = module("homeassistant.util.dt", now=lambda: NOW)
        module("homeassistant.util", dt=dt)
        root = Path(__file__).resolve().parents[1] / "custom_components" / "openligadb"
        module("_offline_openligadb", __path__=[str(root)])
        self.stack.enter_context(patch.dict(sys.modules, modules))
        try:
            for name in ("const", "client", "coordinator", "config_flow", "sensor"):
                fullname = f"_offline_openligadb.{name}"
                spec = importlib.util.spec_from_file_location(fullname, root / f"{name}.py")
                loaded = importlib.util.module_from_spec(spec)
                sys.modules[fullname] = loaded
                spec.loader.exec_module(loaded)
                setattr(self, name, loaded)
            self.stack.enter_context(patch.object(self.coordinator, "datetime", FrozenDateTime))
            self.hass = SimpleNamespace(data={}, session=Mock(), bus=SimpleNamespace(async_fire=Mock()))
            self.stack.enter_context(patch.object(self.client, "REQUEST_INTERVAL", 0))
            self.hass.session.get.side_effect = AssertionError("Unmocked HTTP request")
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


class Response:
    def __init__(self, payload=None, error=None, status=200, headers=None):
        self.payload, self.error = payload, error
        self.status, self.headers = status, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        if self.error:
            raise self.error

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload
