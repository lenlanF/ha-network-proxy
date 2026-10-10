"""Configuration transactions retain supplier policy and protect working state."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from subscription import ConfigError, compose, parse_document
from test_manager import manager

DOCUMENT = {
    "proxies": [{"name": "A", "type": "http", "server": "upstream", "port": 7890}],
    "proxy-groups": [{"name": "Policy", "type": "select", "proxies": ["A"]}],
    "dns": {"enable": True, "nameserver": ["system"], "listen": "0.0.0.0:53"},
    "rules": ["DOMAIN-SUFFIX,supplier.test,Policy", "MATCH,DIRECT"],
    "external-controller": "0.0.0.0:9090",
    "secret": "supplier-secret",
}


class SubscriptionTests(unittest.TestCase):
    def test_overlay_deep_merge_and_rules_before_catchall(self):
        config = compose(
            DOCUMENT,
            "dns:\n  ipv6: true\nappend-rules:\n  - DOMAIN,extra.test,Policy\n",
        )
        self.assertEqual(config["dns"]["nameserver"], ["system"])
        self.assertNotIn("listen", config["dns"])
        self.assertNotIn("secret", config)
        self.assertEqual(
            config["rules"][-2:], ["DOMAIN,extra.test,Policy", "MATCH,DIRECT"]
        )
        self.assertEqual(DOCUMENT["dns"]["listen"], "0.0.0.0:53")

    def test_unsafe_yaml_duplicates_cycles_and_managed_settings_rejected(self):
        for body in (
            "proxies: []\nproxies: []",
            "a: &x [*x]",
            "!!python/object/apply:os.system ['echo bad']",
            "dns: 2026-10-10",
            "a: .nan",
        ):
            with self.subTest(body=body), self.assertRaises(ConfigError):
                parse_document(body)
        for overlay in (
            "tun: {enable: true}",
            "secret: x",
            "dns: {listen: '0.0.0.0:53'}",
        ):
            with self.assertRaises(ConfigError):
                compose(DOCUMENT, overlay)

    def test_provider_paths_cannot_escape_private_directory(self):
        config = compose(
            {
                "proxy-providers": {
                    "../../unsafe": {
                        "type": "http",
                        "url": "https://example.test/sub",
                        "path": "/etc/evil",
                    }
                }
            },
            "",
        )
        path = config["proxy-providers"]["../../unsafe"]["path"]
        self.assertTrue(path.startswith("./providers/proxy-providers-"))
        self.assertNotIn("..", path.removeprefix("./"))
        with self.assertRaises(ConfigError):
            compose(
                {
                    "proxy-providers": {
                        "file": {"type": "file", "path": "/data/options.json"}
                    }
                },
                "",
            )

    def test_first_save_fetches_then_validates_then_applies_and_restart_uses_snapshot(
        self,
    ):
        order = []
        fetcher = Mock(side_effect=lambda url: order.append("fetch") or DOCUMENT)
        with tempfile.TemporaryDirectory() as directory:
            store = manager.SettingsStore(
                directory,
                {"controller_secret": "x" * 24},
                lambda config: order.append("apply"),
                fetcher=fetcher,
                validator=lambda config: order.append("validate"),
            )
            store.update(
                {
                    "subscription_url": "https://subscription.test/token",
                    "extension_yaml": "append-rules:\n  - DOMAIN,extra.test,Policy",
                }
            )
            self.assertEqual(order[0], "fetch")
            self.assertTrue(all(item == "validate" for item in order[1:-1]))
            self.assertEqual(order[-1], "apply")
            self.assertEqual(store.config(store.sources)["rules"][-1], "MATCH,DIRECT")
            restored = manager.SettingsStore(
                directory,
                store.options,
                lambda config: None,
                fetcher=Mock(side_effect=AssertionError("should not fetch")),
            )
            restored.prepare()
            self.assertEqual(restored.snapshot, store.snapshot)

    def test_failed_fetch_and_invalid_override_leave_disk_cache_and_core_unchanged(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            applied = Mock()
            store = manager.SettingsStore(
                directory,
                {"controller_secret": "x" * 24},
                applied,
                fetcher=lambda url: DOCUMENT,
            )
            store.update({"subscription_url": "https://subscription.test/valid"})
            before = {
                path.name: path.read_bytes() for path in Path(directory).iterdir()
            }
            applied.reset_mock()
            for changes in (
                {"extension_yaml": "dns: [bad]"},
                {"subscription_url": "https://subscription.test/broken"},
            ):
                if "subscription_url" in changes:
                    store.fetcher = Mock(
                        side_effect=ConfigError("subscription_fetch_failed")
                    )
                with self.assertRaises(ConfigError):
                    store.update(changes)
                self.assertEqual(
                    {
                        path.name: path.read_bytes()
                        for path in Path(directory).iterdir()
                    },
                    before,
                )
                applied.assert_not_called()

    def test_traffic_failure_restores_core_settings_and_cached_subscription(self):
        with tempfile.TemporaryDirectory() as directory:
            traffic = Mock()
            traffic.state = {}
            traffic.plan.return_value = {"scope": "haos", "ips": [], "tun": True}
            applied = []
            store = manager.SettingsStore(
                directory,
                {"controller_secret": "x" * 24},
                applied.append,
                fetcher=lambda url: DOCUMENT,
            )
            store.update({"lan_host": "192.168.1.2"})
            store.traffic = traffic
            traffic.apply.side_effect = [ConfigError("traffic_apply_failed"), None]
            with self.assertRaises(ConfigError):
                store.update(
                    {
                        "subscription_url": "https://subscription.test/sub",
                        "outlet_source": "subscription",
                        "traffic_mode": "haos",
                    }
                )
            self.assertEqual(store.sources, {"lan_host": "192.168.1.2"})
            self.assertEqual(json.loads(store.path.read_text()), store.sources)
            self.assertEqual(store.snapshot, {})
            self.assertEqual(applied[-1]["proxies"][0]["name"], "LAN")
            self.assertEqual(traffic.apply.call_count, 2)
