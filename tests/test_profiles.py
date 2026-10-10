"""Subscription isolation and management-only behavior with real Mihomo."""

import copy
import socket
import unittest

import test_mihomo


class ProfileCoreTests(unittest.TestCase):
    setUp = test_mihomo.RealCoreTests.setUp
    tearDown = test_mihomo.RealCoreTests.tearDown

    def add(self, name):
        return self.fixture.store.save_subscription(
            {
                "name": name,
                "url": f"http://127.0.0.1:{self.fixture.subscription_port}/subscription",
                "interval": 0,
            }
        )

    def test_switch_closes_listener_and_reopens_without_stopping_management(self):
        self.fixture.set_lan("http")
        self.fixture.store.update({"proxy_enabled": False, "share_enabled": True})
        config = self.fixture.store.runtime_config
        self.assertEqual(config["mixed-port"], 0)
        self.assertFalse(config["tun"]["enable"])
        self.assertFalse(self.fixture.service.status()["proxy_enabled"])
        self.assertEqual(self.fixture.service.runtime()["targets"], [])
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.fixture.proxy_port), timeout=1)
        self.assertIn("version", self.fixture.client.request("GET", "/version"))
        self.assertTrue(
            all(
                check["code"] == "proxy_disabled"
                for check in self.fixture.service.diagnose()["checks"][1:]
            )
        )
        self.fixture.store.update({"proxy_enabled": True, "share_enabled": False})
        self.assertEqual(
            self.fixture.service.test("http://example.com/on")["status"], 200
        )

    def test_profiles_preserve_native_mode_edits_and_selections(self):
        first = self.add("第一份")
        self.fixture.service.select_policy("FixturePolicy", "Fixture-A")
        import yaml

        document = copy.deepcopy(dict(self.fixture.store.snapshot["document"]))
        document["mode"] = "direct"
        self.fixture.store.edit_subscription(first, yaml.safe_dump(document))
        second = self.add("第二份")
        self.fixture.service.subscription_action("activate", {"id": second})
        self.assertEqual(self.fixture.store.runtime_config["mode"], "rule")
        self.assertEqual(
            self.fixture.client.request("GET", "/proxies/FixturePolicy")["now"],
            "Fixture-B",
        )
        self.fixture.service.select_policy("FixturePolicy", "Fixture-A")
        self.fixture.store.update({}, refresh_ids=[first])
        self.fixture.service.subscription_action("activate", {"id": first})
        self.assertEqual(self.fixture.store.runtime_config["mode"], "direct")
        self.assertEqual(
            self.fixture.client.request("GET", "/proxies/FixturePolicy")["now"],
            "Fixture-A",
        )
        self.assertEqual(
            self.fixture.service.subscription_config(first, "original")["config_mode"],
            "edited",
        )
        self.fixture.store.edit_subscription(first, "", native=True)
        self.assertEqual(self.fixture.store.runtime_config["mode"], "rule")
        self.assertIn("edited_yaml", self.fixture.store.snapshots[first])

    def test_invalid_inactive_profile_and_editor_preserve_last_configuration(self):
        first = self.add("可用订阅")
        before = self.fixture.store.path.read_bytes()
        from subscription import ConfigError

        with self.assertRaises(ConfigError):
            self.fixture.store.edit_subscription(first, "proxy-groups: [invalid]")
        self.assertEqual(self.fixture.store.path.read_bytes(), before)
        self.fixture.subscription_document = {
            "proxies": [{"name": "bad", "type": "unsupported"}]
        }
        with self.assertRaises(ConfigError):
            self.add("错误订阅")
        self.assertEqual(self.fixture.store.path.read_bytes(), before)
        self.assertEqual(len(self.fixture.store.sources["subscriptions"]), 1)
        self.assertEqual(
            self.fixture.service.test("http://example.com/still-works")["status"], 200
        )

    def test_remove_last_subscription_leaves_manager_and_controller_available(self):
        identity = self.add("临时订阅")
        self.fixture.store.delete_subscription(identity)
        self.assertFalse(self.fixture.store.sources["proxy_enabled"])
        self.assertEqual(self.fixture.service.subscriptions()["profiles"], [])
        self.assertIn("version", self.fixture.service.status())

    def test_automatic_group_supports_forcing_and_resetting(self):
        self.add("自动组")
        document = copy.deepcopy(dict(self.fixture.store.snapshot["document"]))
        document["proxy-groups"].append(
            {
                "name": "自动选择",
                "type": "url-test",
                "proxies": ["Fixture-A", "Fixture-B"],
                "url": f"http://127.0.0.1:{self.fixture.http_port}/health",
                "interval": 600,
            }
        )
        import yaml

        identity = self.fixture.store.sources["active_subscription"]
        self.fixture.store.edit_subscription(
            identity, yaml.safe_dump(document, allow_unicode=True)
        )
        self.fixture.service.select_policy("自动选择", "Fixture-B")
        self.assertEqual(
            self.fixture.store.snapshot["selections"]["自动选择"], "Fixture-B"
        )
        self.fixture.service.select_policy("自动选择", None, automatic=True)
        self.assertEqual(self.fixture.store.snapshot["selections"]["自动选择"], "")
        self.fixture.store.update({"core_mode": "rule"})
        self.assertEqual(
            self.fixture.client.request(
                "GET",
                "/proxies/"
                + __import__("urllib.parse", fromlist=["quote"]).quote("自动选择"),
            )["type"],
            "URLTest",
        )
