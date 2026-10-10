"""Source persistence, guarded Ingress UI and authenticated integration API."""

import hmac
import html
import http.client
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from generate import generate, source_choice
from subscription import ConfigError, fetch_document, url_digest

VERSION = "0.4.0"
SOURCE_DEFAULTS = {
    "subscription_url": "",
    "subscription_interval": 3600,
    "lan_host": "",
    "lan_port": 7890,
    "lan_type": "http",
    "lan_username": "",
    "lan_password": "",
    "extension_yaml": "",
    "outlet_source": "",
    "core_mode": "rule",
    "traffic_mode": "explicit",
    "selected_apps": [],
    "selected_integrations": [],
    "share_enabled": False,
    "proxy_username": "",
    "proxy_password": "",
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
        if key == "share_enabled":
            if type(value) is not bool:
                raise ValueError("Invalid sharing option")
        elif key in ("selected_apps", "selected_integrations"):
            if (
                not isinstance(value, list)
                or len(value) > 256
                or any(
                    not isinstance(item, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item)
                    for item in value
                )
            ):
                raise ValueError("Invalid targets")
        elif key == "lan_port":
            if type(value) is not int or not 1 <= value <= 65535:
                raise ValueError("Invalid port")
        elif key == "subscription_interval":
            if type(value) is not int or value != 0 and not 300 <= value <= 604800:
                raise ValueError("Invalid subscription interval")
        elif key in RULE_DEFAULTS and key != "default_action":
            if (
                not isinstance(value, list)
                or len(value) > 128
                or any(not isinstance(item, str) for item in value)
            ):
                raise ValueError("Invalid rules")
        elif not isinstance(value, str) or len(value) > (
            131072 if key == "extension_yaml" else 8192
        ):
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
    if bool(sources.get("proxy_username")) != bool(sources.get("proxy_password")):
        raise ValueError("Set both outlet credentials")


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


class SettingsStore:
    def __init__(
        self,
        directory,
        options,
        reload_config,
        *,
        fetcher=fetch_document,
        validator=None,
        traffic=None,
    ):
        self.directory, self.options, self.reload_config = (
            Path(directory),
            options,
            reload_config,
        )
        self.lock = threading.Lock()
        self.path = self.directory / "sources.json"
        self.snapshot_path = self.directory / "subscription.json"
        self.fetcher, self.validator, self.traffic = fetcher, validator, traffic
        self.snapshot = (
            json.loads(self.snapshot_path.read_text())
            if self.snapshot_path.exists()
            else {}
        )
        self.sources = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {key: options[key] for key in SOURCE_KEYS if key in options}
        )
        # One-time migration of former Supervisor business options. Never enable
        # transparent routing as a side effect of upgrading.
        for key in ("proxy_username", "proxy_password"):
            if key not in self.sources and key in options:
                self.sources[key] = options[key]
        validate_sources(self.sources)

    def config(self, sources, snapshot=None):
        base = {
            key: value for key, value in self.options.items() if key not in SOURCE_KEYS
        }
        snapshot = self.snapshot if snapshot is None else snapshot
        url = sources.get("subscription_url", "")
        document = (
            snapshot.get("document")
            if snapshot.get("url_hash") == url_digest(url)
            else None
        )
        return generate(
            {**base, **sources},
            bootstrap=not sources.get("subscription_url")
            and not sources.get("lan_host"),
            subscription_document=document,
        )

    def update(self, changes, *, refresh=False):
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
            if (
                sources.get("proxy_username", "")
                != self.sources.get("proxy_username", "")
                and "proxy_password" not in changes
            ):
                sources.pop("proxy_password", None)
            validate_sources(sources)
            snapshot = self.snapshot
            url = sources.get("subscription_url", "")
            if url and (
                refresh
                or "subscription_url" in changes
                or (
                    source_choice(sources) == "subscription"
                    and snapshot.get("url_hash") != url_digest(url)
                )
            ):
                snapshot = {
                    "url_hash": url_digest(url),
                    "document": self.fetcher(url),
                    "fetched_at": int(time.time()),
                }
                # Validate saved subscriptions even when the active outlet is LAN.
                candidate = self.config(
                    {**sources, "outlet_source": "subscription"}, snapshot
                )
                if self.validator:
                    self.validator(candidate)
            elif not url:
                snapshot = {}
            elif "extension_yaml" in changes and snapshot.get("url_hash") == url_digest(
                url
            ):
                candidate = self.config(
                    {**sources, "outlet_source": "subscription"}, snapshot
                )
                if self.validator:
                    self.validator(candidate)
            new_config = self.config(sources, snapshot)
            old_config = self.config(self.sources)
            if self.validator:
                self.validator(new_config)
            new_plan = self.traffic.plan(sources) if self.traffic else None
            old_plan = (
                (
                    dict(self.traffic.state)
                    or {"scope": "explicit", "ips": [], "tun": False}
                )
                if self.traffic
                else None
            )
            try:
                self.reload_config(new_config)
                if self.traffic:
                    self.traffic.apply(new_plan)
                atomic_json(self.snapshot_path, snapshot)
                atomic_json(self.path, sources)
                atomic_json(self.directory / "config.json", new_config)
            except Exception:
                atomic_json(self.snapshot_path, self.snapshot)
                atomic_json(self.path, self.sources)
                self.reload_config(old_config)
                if self.traffic:
                    self.traffic.apply(old_plan)
                raise
            self.sources = dict(sources)
            self.snapshot = snapshot

    def prepare(self):
        """Restore the last private snapshot without contacting the subscription."""
        url = self.sources.get("subscription_url", "")
        if (
            url
            and source_choice(self.sources) == "subscription"
            and self.snapshot.get("url_hash") != url_digest(url)
        ):
            self.snapshot = {
                "url_hash": url_digest(url),
                "document": self.fetcher(url),
                "fetched_at": int(time.time()),
            }
            atomic_json(self.snapshot_path, self.snapshot)
        if self.traffic:
            self.traffic.cleanup()
            self.traffic.plan(self.sources)
        config = self.config(self.sources)
        if self.validator:
            self.validator(config)
        atomic_json(self.directory / "config.json", config)
        atomic_json(self.path, self.sources)


def validate_mihomo(directory, config, binary="mihomo"):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", dir=directory, delete=False
    ) as handle:
        json.dump(config, handle)
        path = Path(handle.name)
    try:
        result = subprocess.run(
            [binary, "-t", "-d", str(directory), "-f", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
        )
        if result.returncode:
            raise ConfigError("core_validation_failed")
    except subprocess.TimeoutExpired as error:
        raise ConfigError("core_validation_timeout") from error
    finally:
        path.unlink(missing_ok=True)


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
            if not 0 < size <= 262144:
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
                elif service and path == "/api/targets":
                    payload = service.targets()
                elif service and path == "/integration/runtime" and not ingress:
                    payload = service.runtime()
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
                elif (
                    service
                    and method == "POST"
                    and path == "/integration/report"
                    and not ingress
                ):
                    payload = service.report(data)
                else:
                    self.respond(404, {"error": "not_found"})
                    return
            except ConfigError as error:
                self.respond(400, {"error": error.code})
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
    from traffic import Traffic

    os.umask(0o077)
    directory = Path("/data")
    options = json.loads((directory / "options.json").read_text())
    secret = options["controller_secret"]
    client = CoreClient(secret)
    traffic = Traffic(directory)
    store = SettingsStore(
        directory,
        options,
        client.reload,
        validator=lambda config: validate_mihomo(directory, config),
        traffic=traffic,
    )
    store.prepare()
    service = Service(store, client)
    core = subprocess.Popen(["mihomo", "-d", "/data", "-f", "/data/config.json"])
    servers = []
    try:
        for _ in range(100):
            if core.poll() is not None:
                raise RuntimeError("Proxy core exited during startup")
            try:
                client.request("GET", "/version")
                client.set_mode(store.sources.get("core_mode", "rule"))
                traffic.apply(traffic.plan(store.sources))
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Proxy core startup timed out")
        for port, ingress in ((9091, False), (8099, True)):
            server = ThreadingHTTPServer(
                ("0.0.0.0" if ingress else "127.0.0.1", port),
                handler_factory(store, secret, service, ingress=ingress),
            )
            server.daemon_threads = True
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()

        def reconcile():
            while core.poll() is None:
                with store.lock:
                    traffic.refresh_inventory()
                    if store.sources.get(
                        "traffic_mode"
                    ) == "selected" and store.sources.get("selected_apps"):
                        try:
                            plan = traffic.plan(store.sources)
                            if plan != traffic.state:
                                traffic.apply(plan)
                        except (OSError, ValueError):
                            service.record(
                                "error", "应用网络地址同步失败，请检查透明代理状态"
                            )
                time.sleep(15)

        threading.Thread(target=reconcile, daemon=True).start()

        def refresh_subscription():
            while core.poll() is None:
                service.auto_refresh()
                time.sleep(30)

        threading.Thread(target=refresh_subscription, daemon=True).start()

        def stop(_signum, _frame):
            core.terminate()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        raise SystemExit(core.wait())
    finally:
        traffic.cleanup()
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
