import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from subscription import ConfigError
from test_core import load

module = load("traffic", "addons/network_proxy/traffic.py")


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.supervisor = Mock()
        self.supervisor.inventory.return_value = [
            {
                "id": "bridge_app",
                "name": "Bridge",
                "ip": "172.30.33.10",
                "supported": True,
                "state": "started",
            },
            {
                "id": "host_app",
                "name": "Host",
                "ip": None,
                "supported": False,
                "state": "started",
            },
        ]
        self.supervisor.lan_addresses.return_value = ["192.168.1.2"]
        self.traffic = module.Traffic(
            self.temp.name,
            self.supervisor,
            lambda args, **kwargs: (
                self.calls.append(args) or subprocess.CompletedProcess(args, 0)
            ),
        )
        self.traffic.capability = lambda: True

    def tearDown(self):
        self.temp.cleanup()

    def test_host_network_app_cannot_be_selected_and_unprivileged_scope_is_rejected(
        self,
    ):
        with self.assertRaises(ConfigError):
            self.traffic.plan(
                {"traffic_mode": "selected", "selected_apps": ["host_app"]}
            )
        self.traffic.capability = lambda: False
        with self.assertRaises(ConfigError):
            self.traffic.plan({"traffic_mode": "haos"})
        self.assertFalse(
            self.traffic.plan(
                {"traffic_mode": "selected", "selected_integrations": ["entry"]}
            )["tun"]
        )
        self.assertEqual(self.calls, [])

    def test_selected_plan_owns_only_exact_sources_and_cleanup_does_not_flush_other_resources(
        self,
    ):
        plan = self.traffic.plan(
            {"traffic_mode": "selected", "selected_apps": ["bridge_app"]}
        )
        self.traffic.apply(plan)
        self.assertIn(
            [
                "ip",
                "rule",
                "add",
                "pref",
                "20227",
                "from",
                "172.30.33.10/32",
                "lookup",
                "20227",
            ],
            self.calls,
        )
        self.traffic.cleanup()
        self.assertFalse(self.traffic.path.exists())
        for args in self.calls:
            if "-F" in args or "-X" in args:
                self.assertTrue(args[-1].startswith("NP_"))
            if "flush" in args:
                self.assertEqual(args, ["ip", "route", "flush", "table", "20227"])
        self.assertNotIn(["ip", "rule", "flush"], self.calls)

    def test_supervisor_default_role_inventory_does_not_expose_options(self):
        supervisor = module.Supervisor(token="fixture-token")
        supervisor.get = Mock(
            side_effect=[
                {"addons": [{"slug": "app"}]},
                {
                    "name": "App",
                    "state": "started",
                    "host_network": False,
                    "ip_address": "172.30.33.10",
                    "options": {"password": "secret"},
                },
            ]
        )
        result = supervisor.inventory()
        self.assertEqual(supervisor.get.call_args_list[0].args, ("/supervisor/info",))
        self.assertTrue(result[0]["supported"])
        self.assertNotIn("options", result[0])
