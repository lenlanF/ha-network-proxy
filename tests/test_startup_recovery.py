"""Rejected persisted settings retain a reachable, accurately labeled UI."""

import json
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from test_manager import manager
from subscription import ConfigError
from test_application import FakeCore, service_module


class StartupRecoveryTests(unittest.TestCase):
    def test_validation_classifies_error_lines_without_disclosing_private_output(self):
        with tempfile.TemporaryDirectory() as directory:

            def rejected(*args, **kwargs):
                kwargs["stdout"].write(
                    b'level=info msg="GeodataLoader mode"\n'
                    b'level=error msg="proxy group[8]: private-group: use or proxies missing '
                    b'https://supplier.test/token=private-token private-password"\n'
                )
                return SimpleNamespace(returncode=1)

            with (
                patch.object(manager.subprocess, "run", side_effect=rejected),
                self.assertRaises(ConfigError) as result,
            ):
                manager.validate_mihomo(directory, {"secret": "private-secret"})
            self.assertEqual(result.exception.code, "core_validation_proxy_group")
            self.assertNotIn("private-", str(result.exception))
            self.assertEqual(list(Path(directory).iterdir()), [])
            for message, code in (
                ("level=error msg=can't download GeoSite", "geodata"),
                ("level=error msg=permission denied", "permission"),
                ("level=error msg=rules[1]: proxy not found", "rule"),
                ("level=error msg=nameserver invalid", "dns"),
                ("level=error msg=proxy[0]: unsupported proxy type", "proxy"),
            ):
                self.assertEqual(
                    manager.validation_code(message), "core_validation_" + code
                )

    def test_validation_timeout_removes_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    manager.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired("mihomo", 45),
                ),
                self.assertRaises(ConfigError) as result,
            ):
                manager.validate_mihomo(directory, {})
            self.assertEqual(result.exception.code, "core_validation_timeout")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_recovery_retains_sources_disables_capture_and_manual_repair_uses_fallback_rollback(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            sources = {
                "lan_host": "192.168.1.2",
                "traffic_mode": "haos",
                "share_enabled": True,
                "lan_username": "u",
                "lan_password": "private-upstream",
                "selected_integrations": ["weather"],
            }
            path.write_text(json.dumps(sources))
            original = path.read_bytes()
            failed = [True]

            def validate(config):
                if config["proxies"] and failed[0]:
                    raise ConfigError("core_validation_proxy")

            applied = []
            traffic = Mock()
            traffic.state = {}
            traffic.status.return_value = {"active": False}
            store = manager.SettingsStore(
                directory,
                {"controller_secret": "x" * 24},
                applied.append,
                validator=validate,
                traffic=traffic,
            )
            store.prepare_or_recover()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(store.sources, sources)
            self.assertEqual(
                store.runtime_config["proxy-groups"][0]["proxies"], ["REJECT"]
            )
            self.assertFalse(store.runtime_config["tun"]["enable"])
            self.assertFalse(store.runtime_config["allow-lan"])
            traffic.apply.assert_not_called()
            service = service_module.Service(store, FakeCore())
            status = service.status()
            self.assertFalse(status["configured"])
            self.assertEqual(status["traffic_scope"], "explicit")
            self.assertEqual(status["recovery_error"], "core_validation_proxy")
            self.assertNotIn("private-upstream", json.dumps(status))
            self.assertNotIn("lan_password", service.settings())
            for operation in (service.runtime, service.test):
                with self.assertRaises(ConfigError) as result:
                    operation()
                self.assertEqual(result.exception.code, "startup_recovery")
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), manager.handler_factory(store, "x" * 24, service)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/integration/runtime",
                    headers={"Authorization": "Bearer " + "x" * 24},
                )
                with self.assertRaises(HTTPError) as response:
                    build_opener(ProxyHandler({})).open(request, timeout=2)
                self.assertEqual(response.exception.code, 503)
                self.assertEqual(
                    json.load(response.exception)["error"], "startup_recovery"
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            with patch.object(service, "refresh") as refresh:
                service.auto_refresh()
                refresh.assert_not_called()
            # Rejected edits do not erase the stored source or recovery state.
            with self.assertRaises(ConfigError):
                store.update({"core_mode": "rule"})
            self.assertEqual(path.read_bytes(), original)
            failed[0] = False
            traffic.apply.side_effect = [ConfigError("traffic_apply_failed"), None]
            with self.assertRaises(ConfigError):
                store.update({"core_mode": "rule"})
            self.assertEqual(applied[-1]["proxy-groups"][0]["proxies"], ["REJECT"])
            self.assertTrue(store.recovery_error)
            traffic.apply.side_effect = None
            store.update({"core_mode": "rule"})
            self.assertEqual(store.recovery_error, "")
            self.assertEqual(store.sources["traffic_mode"], "explicit")
            self.assertFalse(store.sources["share_enabled"])
            self.assertEqual(store.sources["lan_password"], "private-upstream")

    def test_invalid_new_snapshot_is_not_persisted_before_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = manager.SettingsStore(
                directory,
                {
                    "controller_secret": "x" * 24,
                    "subscription_url": "https://supplier.test/private-token",
                },
                lambda config: None,
                fetcher=lambda url: {
                    "proxies": [
                        {"name": "A", "type": "bad", "server": "upstream", "port": 80}
                    ]
                },
                validator=lambda config: (
                    (_ for _ in ()).throw(ConfigError("core_validation_proxy"))
                    if config["proxies"]
                    else None
                ),
            )
            store.prepare_or_recover()
            self.assertEqual(store.snapshot, {})
            self.assertFalse(store.snapshot_path.exists())
            self.assertTrue(store.recovery_error)
