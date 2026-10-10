import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from test_manager import manager
from traffic import Supervisor


class ListenerPortTests(unittest.TestCase):
    def test_reads_assigned_ingress_port_from_authenticated_supervisor_self_info(self):
        requests = []

        class API(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                body = json.dumps(
                    {"result": "ok", "data": {"ingress_port": 18123}}
                ).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), API)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            supervisor = Supervisor(
                token="fixture-token", url=f"http://127.0.0.1:{server.server_port}"
            )
            self.assertEqual(manager.resolve_ingress_port(supervisor), 18123)
            self.assertEqual(requests, [("/addons/self/info", "Bearer fixture-token")])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_unavailable_or_invalid_supervisor_port_never_falls_back_to_8099(self):
        supervisor = Mock()
        for port in (0, None, True, "18099", 65536, 9091, 7890, 9090, 1053, 8123):
            supervisor.get.return_value = {"ingress_port": port}
            with (
                self.subTest(port=port),
                self.assertRaises(manager.ConfigError) as result,
            ):
                manager.resolve_ingress_port(supervisor)
            self.assertEqual(result.exception.code, "ingress_port_invalid")
        supervisor.get.side_effect = OSError("private-token")
        with self.assertRaises(manager.ConfigError) as result:
            manager.resolve_ingress_port(supervisor)
        self.assertEqual(result.exception.code, "ingress_port_unavailable")

    def test_busy_ingress_reports_exact_port_and_releases_prior_management_listener(
        self,
    ):
        servers = []

        def create(address, handler):
            server = ThreadingHTTPServer(address, handler)
            servers.append(server)
            return server

        with socket.socket() as occupied:
            occupied.bind(("0.0.0.0", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            with (
                patch.object(manager, "ThreadingHTTPServer", side_effect=create),
                self.assertRaises(manager.ConfigError) as result,
            ):
                manager.bind_management_servers(
                    None, "fixture-secret", None, port, management_port=0
                )
            # Windows reports access denied for this reused-address bind;
            # Linux (the production platform) reports EADDRINUSE.
            reason = "bind_failed" if os.name == "nt" else "port_in_use"
            self.assertEqual(result.exception.code, f"ingress_{reason}:{port}")
            self.assertEqual(len(servers), 1)
            with socket.socket() as released:
                released.bind(servers[0].server_address)
            self.assertEqual(occupied.getsockname()[1], port)

    def test_tun_preserves_management_bypass_on_the_assigned_ingress_port(self):
        config = manager.generate(
            {
                "controller_secret": "fixture-controller-secret",
                "lan_host": "192.168.1.2",
                "traffic_mode": "haos",
                "_ingress_port": 18123,
            }
        )
        ports = config["tun"]["exclude-src-port"]
        self.assertIn(18123, ports)
        self.assertIn(9091, ports)
        self.assertNotIn(8099, ports)


if __name__ == "__main__":
    unittest.main()
