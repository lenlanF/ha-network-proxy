"""Source persistence, guarded Ingress UI and authenticated integration API."""

import hmac
import html
import http.client
import json
import os
import re
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from generate import generate

VERSION = "0.3.2"
SOURCE_DEFAULTS = {
    "subscription_url": "",
    "lan_host": "",
    "lan_port": 7890,
    "lan_type": "http",
    "lan_username": "",
    "lan_password": "",
}
RULE_DEFAULTS = {
    "direct_domains": [],
    "proxy_domains": [],
    "direct_cidrs": [],
    "proxy_cidrs": [],
    "default_action": "proxy",
}
SOURCE_KEYS = set(SOURCE_DEFAULTS) | set(RULE_DEFAULTS)


def validate_sources(sources):
    if not isinstance(sources, dict) or set(sources) - SOURCE_KEYS:
        raise ValueError("Invalid source fields")
    for key, value in sources.items():
        if key == "lan_port":
            if type(value) is not int or not 1 <= value <= 65535:
                raise ValueError("Invalid port")
        elif key in RULE_DEFAULTS and key != "default_action":
            if (
                not isinstance(value, list)
                or len(value) > 128
                or any(not isinstance(item, str) for item in value)
            ):
                raise ValueError("Invalid rules")
        elif not isinstance(value, str) or len(value) > 8192:
            raise ValueError("Invalid source value")
    host = sources.get("lan_host", "")
    if host and (any(char in host for char in "/\\@ \r\n") or ":" in host):
        raise ValueError("Use an IPv4 address or hostname without a port")
    if sources.get("lan_type", "http") not in ("http", "socks5"):
        raise ValueError("Invalid proxy protocol")
    if (
        host.lower() in ("localhost", "127.0.0.1", "local-network-proxy")
        and sources.get("lan_port", 7890) == 7890
    ):
        raise ValueError("The application outlet cannot be its own upstream")
    if bool(sources.get("lan_username")) != bool(sources.get("lan_password")):
        raise ValueError("Set both LAN credentials")


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


class SettingsStore:
    def __init__(self, directory, options, reload_config):
        self.directory, self.options, self.reload_config = (
            Path(directory),
            options,
            reload_config,
        )
        self.lock = threading.Lock()
        self.path = self.directory / "sources.json"
        self.sources = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {key: options[key] for key in SOURCE_KEYS if key in options}
        )
        validate_sources(self.sources)

    def config(self, sources):
        base = {
            key: value for key, value in self.options.items() if key not in SOURCE_KEYS
        }
        return generate(
            {**base, **sources},
            bootstrap=not sources.get("subscription_url")
            and not sources.get("lan_host"),
        )

    def update(self, changes):
        if not isinstance(changes, dict) or set(changes) - SOURCE_KEYS:
            raise ValueError("Invalid source fields")
        with self.lock:
            sources = {**self.sources, **changes}
            old_identity = tuple(
                self.sources.get(key, SOURCE_DEFAULTS[key])
                for key in ("lan_host", "lan_port", "lan_type", "lan_username")
            )
            new_identity = tuple(
                sources.get(key, SOURCE_DEFAULTS[key])
                for key in ("lan_host", "lan_port", "lan_type", "lan_username")
            )
            if old_identity != new_identity and "lan_password" not in changes:
                sources.pop("lan_password", None)
            validate_sources(sources)
            new_config = self.config(sources)
            old_config = self.config(self.sources)
            atomic_json(self.path, sources)
            try:
                self.reload_config(new_config)
                atomic_json(self.directory / "config.json", new_config)
            except Exception:
                atomic_json(self.path, self.sources)
                self.reload_config(old_config)
                raise
            self.sources = dict(sources)


def handler_factory(
    store,
    secret,
    service=None,
    *,
    ingress=False,
    allowed_peer="172.30.32.2",
    web_root=None,
):
    web_root = Path(web_root) if web_root else Path(__file__).parent / "web"

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_args):
            pass

        def respond(self, status, payload, content_type="application/json"):
            body = (
                json.dumps(payload).encode()
                if content_type == "application/json"
                else payload
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if ingress:
                if self.client_address[0] != allowed_peer:
                    self.respond(403, {"error": "ingress_only"})
                    return False
                return True
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), f"Bearer {secret}".encode()):
                self.respond(401, {"error": "unauthorized"})
                return False
            return True

        def request_path(self):
            return urlsplit(self.path).path

        def read_body(self):
            if ingress and self.headers.get("X-Proxy-Request") != "1":
                raise ValueError("Missing request marker")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Expected JSON")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 32768:
                raise ValueError("Invalid size")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise TypeError("Expected object")
            return data

        def do_GET(self):
            if not self.authorized():
                return
            path = self.request_path()
            if ingress and path in ("/", "/index.html", "/app.js", "/style.css"):
                self.asset(path)
                return
            try:
                if path == "/health":
                    payload = {"version": VERSION}
                elif service and path == "/api/status":
                    payload = service.status()
                elif service and path == "/api/settings":
                    payload = service.settings()
                elif service and path == "/api/events":
                    payload = {"events": service.event_snapshot()}
                else:
                    self.respond(404, {"error": "not_found"})
                    return
                self.respond(200, payload)
            except (OSError, ValueError, KeyError):
                self.respond(503, {"error": "core_unavailable"})

        def asset(self, path):
            name = "index.html" if path == "/" else path.lstrip("/")
            content_type = {
                "index.html": "text/html; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8",
                "style.css": "text/css; charset=utf-8",
            }[name]
            content = (web_root / name).read_bytes()
            if name == "index.html":
                base = self.headers.get("X-Ingress-Path", "").rstrip("/")
                if not re.fullmatch(r"/[A-Za-z0-9_./-]*", base) and base != "":
                    self.respond(400, {"error": "invalid_base_path"})
                    return
                content = content.replace(
                    b"__BASE__", html.escape(base, quote=True).encode()
                )
            self.respond(200, content, content_type)

        def do_PUT(self):
            self.mutate("PUT")

        def do_POST(self):
            self.mutate("POST")

        def mutate(self, method):
            if not self.authorized():
                return
            path = self.request_path()
            try:
                data = self.read_body()
                if method == "PUT" and path in ("/sources", "/api/settings"):
                    if service:
                        payload = service.update(data)
                    else:
                        store.update(data)
                        payload = {"saved": True}
                elif service and method == "POST" and path == "/api/select":
                    payload = service.select(data.get("name"))
                elif service and method == "POST" and path == "/api/refresh":
                    payload = service.refresh()
                elif service and method == "POST" and path == "/api/discover":
                    payload = service.scan(data.get("cidr", ""))
                elif service and method == "POST" and path == "/api/delay":
                    payload = service.delay(data.get("name"))
                elif service and method == "POST" and path == "/api/test":
                    payload = service.test()
                elif service and method == "POST" and path == "/api/diagnose":
                    payload = service.diagnose()
                else:
                    self.respond(404, {"error": "not_found"})
                    return
            except (ValueError, TypeError, UnicodeDecodeError):
                self.respond(400, {"error": "invalid_settings"})
            except (OSError, http.client.HTTPException) as error:
                from service import failure_code

                code = failure_code(error)
                if service:
                    service.record("error", f"操作失败：{code}；未记录认证信息")
                self.respond(503, {"error": code})
            except KeyError:
                self.respond(503, {"error": "core_incompatible"})
            else:
                self.respond(200, payload)

    return Handler


def main():
    from service import CoreClient, Service

    os.umask(0o077)
    directory = Path("/data")
    options = json.loads((directory / "options.json").read_text())
    secret = options["controller_secret"]
    client = CoreClient(secret)
    store = SettingsStore(directory, options, client.reload)
    atomic_json(directory / "config.json", store.config(store.sources))
    service = Service(store, client)
    core = subprocess.Popen(["mihomo", "-d", "/data", "-f", "/data/config.json"])
    servers = []
    try:
        for port, ingress in ((9091, False), (8099, True)):
            server = ThreadingHTTPServer(
                ("0.0.0.0", port),
                handler_factory(store, secret, service, ingress=ingress),
            )
            server.daemon_threads = True
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()

        def stop(_signum, _frame):
            core.terminate()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        raise SystemExit(core.wait())
    finally:
        if core.poll() is None:
            core.terminate()
            try:
                core.wait(timeout=10)
            except subprocess.TimeoutExpired:
                core.kill()
                core.wait(timeout=5)
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
