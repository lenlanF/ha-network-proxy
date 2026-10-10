import io
import json
import socket
import ssl
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
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
                    "NETWORK_PROXY_OUTLET": {"all": ["LAN"], "now": self.current},
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

    def test_ingress_cannot_read_integration_runtime_credentials(self):
        self.store.update(
            {"proxy_username": "outlet", "proxy_password": "private-outlet-password"}
        )
        for path in ("/integration/runtime", "/api/integration/runtime"):
            with self.assertRaises(HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)
        with self.request("/api/settings") as response:
            settings = json.load(response)
        self.assertNotIn("proxy_password", settings)
        self.assertTrue(settings["has_proxy_password"])
        self.assertEqual(
            self.service.runtime()["proxy_password"], "private-outlet-password"
        )

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
            self.assertEqual(json.load(response)["current"], "lan")
        with self.request("/api/select", "POST", {"name": "lan"}) as response:
            self.assertEqual(json.load(response)["selected"], "lan")

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
            "IP-CIDR6,2001:db8::/32,NETWORK_PROXY_OUTLET,no-resolve",
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

    def test_test_endpoint_classifies_failure_without_leaking_credentials(self):
        failures = [
            (
                HTTPError(
                    "https://token-secret@example.test",
                    407,
                    "secret-lan-password",
                    {},
                    io.BytesIO(),
                ),
                "proxy_auth_required",
            ),
            (
                URLError(ssl.SSLCertVerificationError("secret-lan-password")),
                "tls_certificate_error",
            ),
            (URLError(TimeoutError("secret-lan-password")), "network_timeout"),
            (socket.gaierror("secret-lan-password"), "dns_error"),
            (ConnectionRefusedError("secret-lan-password"), "connection_refused"),
        ]
        for failure, code in failures:
            with (
                self.subTest(code=code),
                patch.object(self.service, "test", side_effect=failure),
            ):
                with self.assertRaises(HTTPError) as error:
                    self.request("/api/test", "POST", {})
                body = error.exception.read().decode()
                self.assertEqual(json.loads(body), {"error": code})
                self.assertNotIn("secret-lan-password", body)
                self.assertNotIn("token-secret", body)
        self.assertNotIn(
            "secret-lan-password", json.dumps(self.service.event_snapshot())
        )

    def test_diagnostic_auth_rejection_is_redacted_and_skips_outlet(self):
        with patch.object(service_module, "HTTPConnection") as connection:
            connection.return_value.getresponse.return_value.status = 407
            with patch.object(self.service, "test") as outlet:
                with self.request("/api/diagnose", "POST", {}) as response:
                    body = response.read().decode()
                outlet.assert_not_called()
            result = json.loads(body)
            self.assertEqual(result["checks"][0]["code"], "proxy_auth_required")
            self.assertEqual(result["checks"][0]["http_status"], 407)
            self.assertFalse(result["checks"][0]["ok"])
            self.assertNotIn("secret-lan-password", body)
            connection.return_value.close.assert_called_once()

    def test_diagnostic_targets_fixed_and_distinguishes_certificate_failure(self):
        with patch.object(service_module, "HTTPConnection") as connection:
            connection.return_value.getresponse.return_value.status = 200
            with patch.object(
                self.service,
                "test",
                side_effect=[
                    URLError(ssl.SSLCertVerificationError("token-secret")),
                    {"status": 200, "latency_ms": 12},
                ],
            ) as outlet:
                with self.request(
                    "/api/diagnose",
                    "POST",
                    {"url": "http://private.test/", "password": "attacker"},
                ) as response:
                    result = json.load(response)
                self.assertEqual(
                    [call.args[0] for call in outlet.call_args_list],
                    [
                        "https://www.gstatic.com/generate_204",
                        "https://example.com/",
                    ],
                )
            self.assertEqual(result["checks"][1]["code"], "tls_certificate_error")
            self.assertTrue(result["checks"][2]["ok"])
            self.assertNotIn("token-secret", json.dumps(result))
