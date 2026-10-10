"""Check connection form behavior without booting Home Assistant."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import voluptuous as vol

ROOT = Path(__file__).resolve().parents[1]


def load_flow():
    """Stub only HA's registration/selector interfaces; execute the real flow."""
    class ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            pass

    modules = {}
    for name in (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.selector",
        "homeassistant.helpers.aiohttp_client",
        "flow_fixture",
        "flow_fixture.api",
        "flow_fixture.const",
    ):
        modules[name] = ModuleType(name)
    modules["homeassistant.config_entries"].ConfigFlow = ConfigFlow
    modules["homeassistant.config_entries"].OptionsFlow = object
    modules["homeassistant.core"].callback = lambda func: func
    selector = modules["homeassistant.helpers.selector"]
    selector.TextSelector = lambda config: str
    selector.TextSelectorConfig = lambda **kwargs: kwargs
    selector.TextSelectorType = type("TextSelectorType", (), {"PASSWORD": "password"})
    modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = None
    modules["flow_fixture.api"].Controller = object
    modules["flow_fixture.api"].Manager = object
    modules["flow_fixture.const"].DOMAIN = "network_proxy"
    spec = importlib.util.spec_from_file_location(
        "flow_fixture.config_flow",
        ROOT / "custom_components/network_proxy/config_flow.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


flow = load_flow()


class ConnectionFormTests(unittest.TestCase):
    def test_unauthed_outlet_credentials_are_optional_in_both_forms(self):
        values = {
            "controller_url": "http://proxy:9090",
            "manager_url": "http://proxy:9091",
            "secret": "test-control-secret-long-enough",
            "proxy_url": "http://proxy:7890",
        }
        for defaults in ({}, {**values, "proxy_username": "", "proxy_password": ""}):
            schema = flow.connection_schema(defaults)
            fields = {str(key): key for key in schema.schema}
            # HA's form renderer uses these markers to set required fields.
            for key in ("proxy_username", "proxy_password"):
                self.assertIsInstance(fields[key], vol.Optional)
                self.assertNotIsInstance(fields[key], vol.Required)
            for key in values:
                self.assertIsInstance(fields[key], vol.Required)
            validated = schema(values)
            self.assertEqual(validated["proxy_username"], "")
            self.assertEqual(validated["proxy_password"], "")
            flow.validate_connection(validated)

    def test_auth_requires_a_complete_pair(self):
        values = {**flow.CONNECTION_DEFAULTS, "secret": "test-control-secret-long-enough"}
        for auth in ({"proxy_username": "u"}, {"proxy_password": "p"}):
            with self.assertRaises(ValueError):
                flow.validate_connection({**values, **auth})
        flow.validate_connection({**values, "proxy_username": "u", "proxy_password": "p"})


if __name__ == "__main__":
    unittest.main()
