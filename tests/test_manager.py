import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_core import load

manager = load("manager", "addons/network_proxy/manager.py")


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.applied = []
        self.options = {"controller_secret": "x" * 24}
        self.store = manager.SettingsStore(
            self.directory, self.options, self.applied.append
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_persists_and_restores_after_restart(self):
        sources = {"lan_host": "192.168.1.2", "lan_type": "socks5", "lan_port": 1080}
        self.store.update(sources)
        restored = manager.SettingsStore(
            self.directory, self.options, self.applied.append
        )
        self.assertEqual(restored.sources, sources)
        config = json.loads((self.directory / "config.json").read_text())
        self.assertEqual(config["proxies"][0]["server"], "192.168.1.2")
        self.assertNotIn("DIRECT", config["proxy-groups"][0]["proxies"])

    def test_apply_failure_restores_previous_disk_and_core(self):
        previous = {"lan_host": "192.168.1.2"}
        self.store.update(previous)
        calls = []

        def fail_new(config):
            calls.append(config)
            if config["proxies"][0]["server"] == "192.168.1.3":
                raise OSError("core unavailable")

        self.store.reload_config = fail_new
        with self.assertRaises(OSError):
            self.store.update({"lan_host": "192.168.1.3"})
        self.assertEqual(json.loads(self.store.path.read_text()), previous)
        self.assertEqual(self.store.sources, previous)
        self.assertEqual(calls[-1]["proxies"][0]["server"], "192.168.1.2")

    def test_bootstrap_rejects_traffic_and_clear_does_not_restore_old_source(self):
        self.store.options["subscription_url"] = "https://example.com/old"
        config = self.store.config({})
        self.assertNotIn("proxy-providers", config)
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["REJECT"])

    def test_rejects_unknown_fields_and_malformed_hosts(self):
        for value in (
            {"secret": "overwrite"},
            {"lan_host": "http://example.com"},
            {"lan_port": True},
            {"lan_port": 0},
            {"lan_username": "u"},
            {"lan_type": "invalid"},
        ):
            with self.assertRaises(ValueError):
                self.store.update(value)
        self.assertEqual(self.applied, [])

    def test_http_authentication_and_no_secret_read_endpoint(self):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            manager.handler_factory(self.store, self.options["controller_secret"]),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/health", timeout=2)
            self.assertEqual(error.exception.code, 401)
            headers = {"Authorization": "Bearer " + self.options["controller_secret"]}
            with urlopen(
                Request(base + "/health", headers=headers), timeout=2
            ) as response:
                self.assertEqual(json.load(response)["version"], manager.VERSION)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + "/sources", headers=headers), timeout=2)
            self.assertEqual(error.exception.code, 404)
            request = Request(
                base + "/sources",
                method="PUT",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"lan_host": "192.168.1.2"}).encode(),
            )
            with urlopen(request, timeout=2) as response:
                self.assertTrue(json.load(response)["saved"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
