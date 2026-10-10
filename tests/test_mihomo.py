import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

CORE = Path(os.environ.get("MIHOMO_BINARY", Path(__file__).resolve().parents[1] / ".tools" / "mihomo-windows-amd64-v1.exe"))


@unittest.skipUnless(
    CORE.exists(), "Place the official Mihomo binary in .tools to run real-core tests"
)
class RealCoreTests(unittest.TestCase):
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
        self.assertEqual(self.fixture.service.status()["current"], "LAN")

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

    def test_subscription_load_refresh_and_node_switch(self):
        self.fixture.store.update(
            {
                "subscription_url": f"http://127.0.0.1:{self.fixture.subscription_port}/subscription",
                "lan_host": "",
                "lan_username": "",
                "lan_password": "",
            }
        )
        for _ in range(50):
            if len(self.fixture.service.status()["nodes"]) == 2:
                break
            time.sleep(0.1)
        self.fixture.service.select("Fixture-B")
        self.fixture.service.refresh()
        self.assertEqual(self.fixture.service.status()["current"], "Fixture-B")
        self.assertEqual(
            self.fixture.service.test("http://example.com/subscription")["status"], 200
        )
        self.assertIn("CONNECT example.com:80", self.fixture.targets)
        self.assertIn("/subscription", self.fixture.targets)
