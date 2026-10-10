"""Local-only protocol fixtures and a real Mihomo process for integration QA."""

import base64
import json
import os
import select
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from test_application import service_module
from test_manager import manager

ROOT = Path(__file__).resolve().parents[1]


def relay(left, right):
    with right:
        while True:
            ready, _, _ = select.select([left, right], [], [], 10)
            if not ready:
                return
            for source in ready:
                try:
                    data = source.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                try:
                    (right if source is left else left).sendall(data)
                except OSError:
                    return


def reserve_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.servers = []
        self.threads = []
        self.targets = []
        self.process = None
        self.log = None
        self.exe = Path(
            os.environ.get(
                "MIHOMO_BINARY", ROOT / ".tools" / "mihomo-windows-amd64-v1.exe"
            )
        )
        self.secret = "fixture-secret-at-least-16"

    def start_server(self, server):
        server.daemon_threads = True
        self.servers.append(server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.threads.append(thread)
        thread.start()
        return server.server_address[1]

    def start(self):
        fixture = self

        class Origin(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                fixture.targets.append(self.path)
                body = b"fixture-response"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.origin_port = self.start_server(
            ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.com")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(days=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("example.com"), x509.DNSName("www.gstatic.com")]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_path, key_path = self.directory / "cert.pem", self.directory / "key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        self.ssl_context = ssl.create_default_context(cafile=str(cert_path))
        secure_origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        secure_origin.socket = context.wrap_socket(
            secure_origin.socket, server_side=True
        )
        self.tls_port = self.start_server(secure_origin)

        class SharedHTTP(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def authenticate(self):
                expected = (
                    "Basic " + base64.b64encode(b"lan-user:lan-password").decode()
                )
                if self.headers.get("Proxy-Authorization") != expected:
                    self.send_response(407)
                    self.send_header("Proxy-Authenticate", 'Basic realm="fixture"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
                return True

            def do_GET(self):
                if not self.authenticate():
                    return
                fixture.targets.append(self.path)
                body = b"fixture-response"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_CONNECT(self):
                if not self.authenticate():
                    return
                fixture.targets.append("CONNECT " + self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.flush()
                destination = (
                    fixture.tls_port
                    if self.path.endswith(":443")
                    else fixture.origin_port
                )
                upstream = socket.create_connection(
                    ("127.0.0.1", destination), timeout=5
                )
                relay(self.connection, upstream)
                self.close_connection = True

        self.http_port = self.start_server(
            ThreadingHTTPServer(("127.0.0.1", 0), SharedHTTP)
        )

        class SharedSOCKS(StreamRequestHandler):
            def handle(self):
                self.request.settimeout(5)
                greeting = self.rfile.read(2)
                if len(greeting) != 2 or greeting[0] != 5:
                    return
                methods = self.rfile.read(greeting[1])
                if 2 not in methods:
                    self.wfile.write(b"\x05\xff")
                    return
                self.wfile.write(b"\x05\x02")
                auth = self.rfile.read(2)
                if len(auth) != 2:
                    return
                username = self.rfile.read(auth[1])
                size = self.rfile.read(1)
                if not size:
                    return
                password = self.rfile.read(size[0])
                if username != b"lan-user" or password != b"lan-password":
                    self.wfile.write(b"\x01\x01")
                    return
                self.wfile.write(b"\x01\x00")
                request = self.rfile.read(4)
                if len(request) != 4 or request[:2] != b"\x05\x01":
                    return
                if request[3] == 3:
                    host = self.rfile.read(self.rfile.read(1)[0]).decode()
                elif request[3] == 1:
                    host = socket.inet_ntoa(self.rfile.read(4))
                elif request[3] == 4:
                    host = socket.inet_ntop(socket.AF_INET6, self.rfile.read(16))
                else:
                    return
                port = int.from_bytes(self.rfile.read(2), "big")
                fixture.targets.append(f"SOCKS {host}:{port}")
                upstream = socket.create_connection(
                    (
                        "127.0.0.1",
                        fixture.tls_port if port == 443 else fixture.origin_port,
                    ),
                    timeout=5,
                )
                self.wfile.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                relay(self.request, upstream)

        self.socks_port = self.start_server(
            ThreadingTCPServer(("127.0.0.1", 0), SharedSOCKS)
        )

        class Subscription(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                proxies = [
                    {
                        "name": name,
                        "type": "http",
                        "server": "127.0.0.1",
                        "port": fixture.http_port,
                        "username": "lan-user",
                        "password": "lan-password",
                    }
                    for name in ("Fixture-A", "Fixture-B")
                ]
                body = json.dumps(
                    {
                        "proxies": proxies,
                        "proxy-groups": [
                            {
                                "name": "FixturePolicy",
                                "type": "select",
                                "proxies": ["Fixture-B", "Fixture-A"],
                            }
                        ],
                        "rules": [
                            "DOMAIN-SUFFIX,example.com,FixturePolicy",
                            "DOMAIN-SUFFIX,gstatic.com,FixturePolicy",
                            "MATCH,REJECT",
                        ],
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.subscription_port = self.start_server(
            ThreadingHTTPServer(("127.0.0.1", 0), Subscription)
        )
        self.controller_port, self.proxy_port = reserve_port(), reserve_port()
        self.client = service_module.CoreClient(
            self.secret, f"http://127.0.0.1:{self.controller_port}"
        )

        def config_for_test(config):
            return {
                **config,
                "external-controller": f"127.0.0.1:{self.controller_port}",
                "mixed-port": self.proxy_port,
                "bind-address": "127.0.0.1",
            }

        self.store = manager.SettingsStore(
            self.directory,
            {
                "controller_secret": self.secret,
                "proxy_username": "outlet-user",
                "proxy_password": "outlet-password",
            },
            lambda config: self.client.reload(config_for_test(config)),
            validator=lambda config: manager.validate_mihomo(
                self.directory, config_for_test(config), str(self.exe)
            ),
        )
        config = config_for_test(self.store.config({}))
        path = self.directory / "config.json"
        path.write_text(json.dumps(config))
        self.log = (self.directory / "core.log").open("wb")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            [str(self.exe), "-d", str(self.directory), "-f", str(path)],
            stdout=self.log,
            stderr=self.log,
            creationflags=flags,
        )
        for _ in range(100):
            try:
                self.client.request("GET", "/version")
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Mihomo fixture did not start")
        self.service = service_module.Service(
            self.store, self.client, f"http://127.0.0.1:{self.proxy_port}"
        )
        return self

    def set_lan(self, protocol):
        self.store.update(
            {
                "subscription_url": "",
                "lan_host": "127.0.0.1",
                "lan_port": self.http_port if protocol == "http" else self.socks_port,
                "lan_type": protocol,
                "lan_username": "lan-user",
                "lan_password": "lan-password",
                "outlet_source": "lan",
            }
        )

    def close(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=10)
        if self.log:
            self.log.close()
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=3)
        self.temp.cleanup()
