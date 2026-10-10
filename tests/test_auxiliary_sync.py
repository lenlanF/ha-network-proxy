"""Exercise the real companion setup with small HA lifecycle stubs."""

import importlib.util
import sys
import unittest
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientSession


def load_integration():
    modules = {
        name: ModuleType(name)
        for name in (
            "homeassistant",
            "homeassistant.config_entries",
            "homeassistant.const",
            "homeassistant.helpers",
            "homeassistant.helpers.aiohttp_client",
            "homeassistant.helpers.update_coordinator",
            "aux_fixture.api",
            "aux_fixture.const",
        )
    }
    modules["homeassistant.config_entries"].current_entry = ContextVar(
        "entry", default=None
    )
    modules["homeassistant.const"].Platform = SimpleNamespace(
        SELECT="select", SENSOR="sensor", BUTTON="button"
    )
    modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = (
        lambda hass: None
    )

    class UpdateFailed(Exception):
        pass

    class Coordinator:
        def __init__(self, hass, logger, **kwargs):
            self.update = kwargs["update_method"]
            self.interval = kwargs["update_interval"]

        async def async_config_entry_first_refresh(self):
            self.data = await self.update()

    class Manager:
        def __init__(self, *args):
            self.settings = {
                "traffic_mode": "selected",
                "targets": ["selected"],
                "proxy_url": "http://127.0.0.1:7890",
                "proxy_username": "",
                "proxy_password": "",
            }
            self.report = AsyncMock()
            self.status = AsyncMock(return_value={"version": "fixture"})

        async def runtime(self):
            return self.settings

    modules[
        "homeassistant.helpers.update_coordinator"
    ].DataUpdateCoordinator = Coordinator
    modules["homeassistant.helpers.update_coordinator"].UpdateFailed = UpdateFailed
    modules["aux_fixture.api"].Manager = Manager
    modules["aux_fixture.const"].DOMAIN = "network_proxy"
    path = Path(__file__).resolve().parents[1] / "custom_components/network_proxy"
    spec = importlib.util.spec_from_file_location(
        "aux_fixture", path / "__init__.py", submodule_search_locations=[str(path)]
    )
    module = importlib.util.module_from_spec(spec)
    modules["aux_fixture"] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module, UpdateFailed


integration, UpdateFailed = load_integration()


class AuxiliaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = SimpleNamespace(
            entry_id="owner",
            title="Proxy",
            domain="network_proxy",
            disabled_by=None,
            version=1,
            data={
                "controller_url": "http://local-network-proxy:9090",
                "manager_url": "http://local-network-proxy:9091",
                "proxy_url": "http://local-network-proxy:7890",
                "secret": "fixture-secret-at-least-16",
            },
            options={},
            async_on_unload=Mock(),
            add_update_listener=Mock(),
        )
        entries = [
            self.entry,
            SimpleNamespace(
                entry_id="selected",
                title="Selected",
                domain="example",
                disabled_by=None,
            ),
            SimpleNamespace(
                entry_id="disabled",
                title="Disabled",
                domain="example",
                disabled_by="user",
            ),
        ]
        self.hass = SimpleNamespace(
            data={},
            config_entries=SimpleNamespace(
                async_entries=lambda: entries,
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True),
                async_update_entry=Mock(),
            ),
        )

    async def asyncTearDown(self):
        integration._ROUTER.remove("owner")

    async def test_webpage_policy_sync_preserves_counters_and_unload_restores_hook(
        self,
    ):

        original = ClientSession._request
        await integration.async_setup_entry(self.hass, self.entry)
        runtime = self.hass.data["network_proxy"]["owner"]
        policy, coordinator, api = (
            runtime["policy"],
            runtime["coordinator"],
            runtime["api"],
        )
        self.assertEqual(policy.targets, {"selected"})
        self.assertEqual(coordinator.interval.total_seconds(), 10)
        self.assertEqual(len(api.report.call_args.args[0]), 2)
        self.assertIsNot(ClientSession._request, original)
        policy.attempts["selected"] = 7
        api.settings.update(
            proxy_username="local-user", proxy_password="local-password"
        )
        await coordinator.update()
        self.assertEqual(policy.auth.login, "local-user")
        self.assertEqual(policy.attempts["selected"], 7)
        api.runtime = AsyncMock(side_effect=ValueError("offline"))
        with self.assertRaises(UpdateFailed):
            await coordinator.update()
        self.assertEqual(policy.targets, {"selected"})
        await integration.async_unload_entry(self.hass, self.entry)
        self.assertIs(ClientSession._request, original)

    async def test_scope_change_removes_per_integration_hook(self):

        original = ClientSession._request
        await integration.async_setup_entry(self.hass, self.entry)
        runtime = self.hass.data["network_proxy"]["owner"]
        runtime["api"].settings["traffic_mode"] = "haos"
        await runtime["coordinator"].update()
        self.assertEqual(runtime["policy"].targets, set())
        self.assertIs(ClientSession._request, original)

    async def test_legacy_connection_migrates_without_copying_old_business_options(
        self,
    ):
        self.entry.options = {
            "experimental_routing": True,
            "targets": ["selected"],
            "proxy_password": "old-auth",
        }
        self.assertTrue(await integration.async_migrate_entry(self.hass, self.entry))
        changes = self.hass.config_entries.async_update_entry.call_args.kwargs
        self.assertEqual(changes["version"], 2)
        self.assertEqual(changes["data"]["manager_url"], "http://127.0.0.1:9091")
        self.assertNotIn("proxy_password", changes["data"])
        self.assertEqual(changes["options"], {})
