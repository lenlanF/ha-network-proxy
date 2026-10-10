import os
import ssl
import unittest
from pathlib import Path
from unittest.mock import patch

CORE = Path(
    os.environ.get(
        "MIHOMO_BINARY",
        Path(__file__).resolve().parents[1] / ".tools" / "mihomo-windows-amd64-v1.exe",
    )
)


@unittest.skipUnless(
    CORE.exists(), "Place the official Mihomo binary in .tools to run real-core tests"
)
class RealCoreTests(unittest.TestCase):
    def test_emoji_subscription_save_reload_and_persisted_restart(self):
        from urllib.parse import quote

        from manager import SettingsStore

        node, group = "🇭🇰香港节点🚀", "🌍订阅策略"
        self.fixture.subscription_document = {
            "proxies": [
                {
                    "name": node,
                    "type": "http",
                    "server": "127.0.0.1",
                    "port": self.fixture.http_port,
                    "username": "lan-user",
                    "password": "lan-password",
                }
            ],
            "proxy-groups": [{"name": group, "type": "select", "proxies": [node]}],
            "rules": [f"MATCH,{group}"],
        }
        self.fixture.store.update(
            {
                "subscription_url": f"http://127.0.0.1:{self.fixture.subscription_port}/subscription",
                "outlet_source": "subscription",
            }
        )
        self.assertEqual(
            self.fixture.client.request("GET", "/proxies/" + quote(group, safe=""))[
                "now"
            ],
            node,
        )
        self.assertEqual(
            self.fixture.service.test("http://example.com/emoji-save")["status"], 200
        )
        restored = SettingsStore(
            self.fixture.directory,
            self.fixture.store.options,
            self.fixture.store.reload_config,
            validator=self.fixture.store.validator,
        )
        restored.prepare()
        restored.reload_config(restored.runtime_config)
        self.assertEqual(
            self.fixture.client.request("GET", "/proxies/" + quote(group, safe=""))[
                "now"
            ],
            node,
        )
        self.assertEqual(
            self.fixture.service.test("http://example.com/emoji-restart")["status"], 200
        )

    def test_saved_bad_group_recovers_with_reject_outlet_then_repairs(self):
        from subscription import ConfigError

        self.fixture.store.update(
            {
                "subscription_url": f"http://127.0.0.1:{self.fixture.subscription_port}/subscription",
                "outlet_source": "subscription",
            }
        )
        self.fixture.store.sources["extension_yaml"] = (
            "proxy-groups:\n  - name: Broken\n    type: select"
        )
        from manager import atomic_json

        atomic_json(self.fixture.store.path, self.fixture.store.sources)
        original = self.fixture.store.path.read_bytes()
        self.fixture.store.prepare_or_recover()
        self.fixture.store.reload_config(self.fixture.store.runtime_config)
        self.assertEqual(self.fixture.store.path.read_bytes(), original)
        self.assertEqual(
            self.fixture.store.recovery_error, "core_validation_proxy_group"
        )
        self.assertFalse(self.fixture.service.status()["configured"])
        group = self.fixture.client.request("GET", "/proxies/NETWORK_PROXY_OUTLET")
        self.assertEqual(group["now"], "REJECT")
        with self.assertRaises(ConfigError):
            self.fixture.service.runtime()
        self.fixture.store.update({"extension_yaml": ""})
        self.assertEqual(self.fixture.store.recovery_error, "")
        self.assertEqual(
            self.fixture.service.test("http://example.com/repaired")["status"], 200
        )

    def setUp(self):
        from core_fixture import Fixture

        self.fixture = Fixture()
        try:
            self.fixture.start()
        except Exception:
            self.fixture.close()
            raise

    def tearDown(self):
        self.fixture.close()

    def test_authenticated_http_and_https_connect_outlet(self):
        self.fixture.set_lan("http")
        with patch.dict(os.environ, {"NO_PROXY": "*"}):
            result = self.fixture.service.test("http://example.com/plain")
            self.assertEqual(result["status"], 200)
            secure = self.fixture.service.test(
                "https://example.com/secure", ssl_context=self.fixture.ssl_context
            )
            self.assertEqual(secure["status"], 200)
        self.assertIn("CONNECT example.com:80", self.fixture.targets)
        self.assertIn("/plain", self.fixture.targets)
        self.assertIn("CONNECT example.com:443", self.fixture.targets)
        self.assertEqual(self.fixture.service.status()["current"], "lan")

    def test_authenticated_socks5_http_and_https(self):
        self.fixture.set_lan("socks5")
        self.assertEqual(
            self.fixture.service.test("http://example.com/plain")["status"], 200
        )
        self.assertEqual(
            self.fixture.service.test(
                "https://example.com/secure", ssl_context=self.fixture.ssl_context
            )["status"],
            200,
        )
        self.assertIn("SOCKS example.com:80", self.fixture.targets)
        self.assertIn("SOCKS example.com:443", self.fixture.targets)

    def test_real_diagnostic_auth_and_verified_tls_targets(self):
        self.fixture.set_lan("http")
        original = self.fixture.service.test
        with patch.object(
            self.fixture.service,
            "test",
            side_effect=lambda target: original(
                target,
                ssl_context=ssl.create_default_context()
                if "gstatic" in target
                else self.fixture.ssl_context,
            ),
        ):
            result = self.fixture.service.diagnose()
        self.assertTrue(result["checks"][0]["ok"])
        # One request uses the system CA store and must reject the local test
        # certificate; the second uses the explicit test CA and must pass.
        self.assertEqual(result["checks"][1]["code"], "tls_certificate_error")
        self.assertTrue(result["checks"][2]["ok"])
        self.fixture.store.update({"lan_password": "incorrect-test-password"})
        rejected = self.fixture.service.diagnose()
        self.assertEqual(len(rejected["checks"]), 1)
        self.assertEqual(rejected["checks"][0]["code"], "proxy_auth_required")

    def test_subscription_load_refresh_and_source_switch(self):
        self.fixture.store.update(
            {
                "subscription_url": f"http://127.0.0.1:{self.fixture.subscription_port}/subscription",
                "lan_host": "",
                "lan_username": "",
                "lan_password": "",
                "outlet_source": "subscription",
            }
        )
        self.assertEqual(len(self.fixture.service.status()["outlets"]), 2)
        self.assertNotIn("nodes", self.fixture.service.status())
        self.fixture.service.select("subscription")
        self.fixture.service.refresh()
        self.assertEqual(self.fixture.service.status()["current"], "subscription")
        self.assertEqual(
            self.fixture.service.test("http://example.com/subscription")["status"], 200
        )
        self.assertIn("CONNECT example.com:80", self.fixture.targets)
        self.assertIn("/subscription", self.fixture.targets)

    def test_full_subscription_override_and_bad_group_roll_back_without_losing_outlet(
        self,
    ):
        self.fixture.store.update(
            {
                "subscription_url": f"http://127.0.0.1:{self.fixture.subscription_port}/subscription",
                "outlet_source": "subscription",
                "extension_yaml": "append-rules:\n  - DOMAIN,override.test,FixturePolicy",
            }
        )
        rules = self.fixture.store.config(self.fixture.store.sources)["rules"]
        self.assertEqual(rules[-1], "MATCH,REJECT")
        self.assertEqual(rules[-2], "DOMAIN,override.test,FixturePolicy")
        self.assertEqual(
            self.fixture.service.test("http://override.test/preserved-policy")[
                "status"
            ],
            200,
        )
        from subscription import ConfigError

        with self.assertRaises(ConfigError):
            self.fixture.store.update(
                {"extension_yaml": "proxy-groups:\n  - name: Missing\n    type: select"}
            )
        self.assertEqual(
            self.fixture.service.test("http://example.com/after-rejected-save")[
                "status"
            ],
            200,
        )

    def test_global_mode_selects_managed_outlet_instead_of_direct(self):
        self.fixture.set_lan("http")
        self.fixture.store.update({"core_mode": "global"})
        self.assertEqual(
            self.fixture.client.request("GET", "/proxies/GLOBAL")["now"],
            "NETWORK_PROXY_OUTLET",
        )
        self.assertEqual(
            self.fixture.service.test("http://example.com/global")["status"], 200
        )
