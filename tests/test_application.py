import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_core import load
from test_manager import manager

service_module = load("service", "addons/network_proxy/service.py")


class FakeCore:
    def __init__(self):
        self.current = "LAN"
        self.calls = []

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if path == "/version":
            return {"version": "test"}
        if path == "/proxies":
            return {
                "proxies": {
                    "PROXY": {"all": ["LAN"], "now": self.current},
                    "LAN": {"type": "Http", "history": []},
                }
            }
        if path == "/proxies/PROXY":
            if method == "PUT":
                self.current = data["name"]
                return None
            return {"all": ["LAN"], "now": self.current}
        return None


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.secret = "test-secret-at-least-16"
        self.core = FakeCore()
        self.store = manager.SettingsStore(
            self.temp.name, {"controller_secret": self.secret}, lambda _config: None
        )
        self.store.update(
            {
                "lan_host": "192.168.1.2",
                "lan_username": "u",
                "lan_password": "secret-lan-password",
            }
        )
        self.service = service_module.Service(self.store, self.core)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            manager.handler_factory(
                self.store,
                self.secret,
                self.service,
                ingress=True,
                allowed_peer="127.0.0.1",
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method="GET", data=None, headers=None):
        headers = (
            headers
            if headers is not None
            else {"Content-Type": "application/json", "X-Proxy-Request": "1"}
        )
        request = Request(
            self.base + path,
            method=method,
            headers=headers,
            data=json.dumps(data).encode() if data is not None else None,
        )
        return urlopen(request, timeout=3)

    def test_ingress_assets_handle_path_prefix_without_secrets(self):
        with self.request(
            "/", headers={"X-Ingress-Path": "/api/hassio_ingress/test-session"}
        ) as response:
            body = response.read().decode()
            self.assertIn("/api/hassio_ingress/test-session/app.js", body)
            self.assertNotIn(self.secret, body)
            self.assertNotIn("secret-lan-password", body)
        with self.request("/app.js") as response:
            self.assertIn("text/javascript", response.headers["Content-Type"])
        with self.assertRaises(HTTPError) as error:
            self.request("/../manager.py")
        self.assertEqual(error.exception.code, 404)

    def test_settings_redact_password_and_partial_rule_save_preserves_it(self):
        with self.request("/api/settings") as response:
            settings = json.load(response)
        self.assertTrue(settings["has_lan_password"])
        self.assertNotIn("lan_password", settings)
        with self.request(
            "/api/settings", "PUT", {"direct_domains": ["example.cn"]}
        ) as response:
            self.assertTrue(json.load(response)["saved"])
        self.assertEqual(self.store.sources["lan_password"], "secret-lan-password")
        self.assertEqual(self.store.sources["direct_domains"], ["example.cn"])

    def test_changed_proxy_does_not_inherit_credentials(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/api/settings", "PUT", {"lan_host": "192.168.1.3"})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.store.sources["lan_host"], "192.168.1.2")

    def test_csrf_missing_marker_and_unknown_node_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request(
                "/api/settings",
                "PUT",
                {"lan_host": "192.168.1.3"},
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.request("/api/select", "POST", {"name": "missing"})
        self.assertEqual(error.exception.code, 400)
        self.assertNotIn(
            ("PUT", "/proxies/PROXY", {"name": "missing"}), self.core.calls
        )

    def test_status_and_node_selection(self):
        with self.request("/api/status") as response:
            self.assertEqual(json.load(response)["current"], "LAN")
        with self.request("/api/select", "POST", {"name": "LAN"}) as response:
            self.assertEqual(json.load(response)["selected"], "LAN")

    def test_default_ingress_rejects_local_peer_even_with_spoofed_header(self):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            manager.handler_factory(
                self.store, self.secret, self.service, ingress=True
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/settings",
                headers={
                    "X-Forwarded-For": "172.30.32.2",
                    "Authorization": f"Bearer {self.secret}",
                },
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_invalid_domain_and_ipv6_rule_generation(self):
        with self.assertRaises(ValueError):
            self.store.update({"proxy_domains": ["example.com,DIRECT"]})
        self.store.update({"proxy_cidrs": ["2001:db8::/32"]})
        self.assertIn(
            "IP-CIDR6,2001:db8::/32,PROXY,no-resolve",
            self.store.config(self.store.sources)["rules"],
        )

    def test_self_proxy_loop_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update(
                {
                    "lan_host": "localhost",
                    "lan_port": 7890,
                    "lan_username": "",
                    "lan_password": "",
                }
            )
