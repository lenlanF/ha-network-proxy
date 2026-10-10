"""Check connection form behavior without booting Home Assistant."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock, patch

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
    def test_outlet_credentials_are_owned_by_webpage_not_connection_form(self):
        values = {
            "controller_url": "http://proxy:9090",
            "manager_url": "http://proxy:9091",
            "secret": "test-control-secret-long-enough",
            "proxy_url": "http://proxy:7890",
        }
        for defaults in ({}, {**values, "proxy_username": "", "proxy_password": ""}):
            schema = flow.connection_schema(defaults)
            fields = {str(key): key for key in schema.schema}
            for key in ("proxy_username", "proxy_password"):
                self.assertNotIn(key, fields)
            for key in values:
                self.assertIsInstance(fields[key], vol.Required)
            validated = schema(values)
            flow.validate_connection(validated)

    def test_internal_addresses_use_host_network_loopback(self):
        values = {
            **flow.CONNECTION_DEFAULTS,
            "secret": "test-control-secret-long-enough",
        }
        flow.validate_connection(values)
        for key in ("controller_url", "manager_url", "proxy_url"):
            self.assertIn("127.0.0.1", values[key])


class ConnectionValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_managed_application_connects_without_proxy_named_group_even_when_disabled(
        self,
    ):
        values = {**flow.CONNECTION_DEFAULTS, "secret": "fixture-secret-at-least-16"}
        controller = Mock(status=AsyncMock(return_value={"version": "v1.19.12"}))
        manager = Mock(
            health=AsyncMock(
                return_value={
                    "version": "0.5.0",
                    "application": "network_proxy",
                    "features": ["proxy_switch"],
                }
            ),
            status=AsyncMock(return_value={"proxy_enabled": False}),
        )
        with (
            patch.object(flow, "async_get_clientsession", return_value=object()),
            patch.object(flow, "Controller", return_value=controller),
            patch.object(flow, "Manager", return_value=manager),
        ):
            await flow.validate_controller(object(), values)
        controller.status.assert_awaited_once()
        manager.status.assert_awaited_once()

    async def test_old_or_unrelated_manager_is_rejected(self):
        values = {**flow.CONNECTION_DEFAULTS, "secret": "fixture-secret-at-least-16"}
        with (
            patch.object(flow, "async_get_clientsession", return_value=object()),
            patch.object(flow, "Controller", return_value=Mock(status=AsyncMock())),
            patch.object(
                flow,
                "Manager",
                return_value=Mock(health=AsyncMock(return_value={"version": "0.4.3"})),
            ),self.assertRaises(ValueError)
        ):
            await flow.validate_controller(object(), values)


if __name__ == "__main__":
    unittest.main()
