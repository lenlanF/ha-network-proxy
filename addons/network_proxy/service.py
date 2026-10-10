"""UI-facing operations, with no generic controller API passthrough."""

import asyncio
import base64
import http.client
import json
import socket
import ssl
import threading
import time
from collections import deque
from datetime import UTC, datetime
from http.client import HTTPConnection
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from discovery import discover
from generate import ROOT_GROUP, source_choice
from subscription import ConfigError


def failure_code(error):
    """Return only allowlisted reasons; exception text may contain credentials."""
    if isinstance(error, HTTPError):
        return "proxy_auth_required" if error.code == 407 else "http_error"
    if isinstance(error, URLError):
        error = error.reason
    if isinstance(error, ssl.SSLCertVerificationError):
        return "tls_certificate_error"
    if isinstance(error, ssl.SSLError):
        return "tls_error"
    if isinstance(error, TimeoutError):
        return "network_timeout"
    if isinstance(error, socket.gaierror):
        return "dns_error"
    if isinstance(error, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(error, (ConnectionResetError, http.client.RemoteDisconnected)):
        return "connection_closed"
    return "network_error"


class ForcedProxyHandler(ProxyHandler):
    """Keep the outlet test proxied even when the environment has NO_PROXY."""

    def proxy_open(self, request, proxy, _type):
        parsed = urlsplit(proxy)
        if parsed.username:
            raise ValueError("Credentials must be sent as proxy headers")
        request.set_proxy(parsed.netloc, parsed.scheme)


class CoreClient:
    def __init__(self, secret, url="http://127.0.0.1:9090"):
        self.secret, self.url = secret, url.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, data=None):
        request = Request(
            self.url + path,
            method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Authorization": f"Bearer {self.secret}",
                "Content-Type": "application/json",
            },
        )
        with self.opener.open(request, timeout=30) as response:
            return json.load(response) if response.status != 204 else None

    def reload(self, config):
        self.request("PUT", "/configs?force=true", {"payload": json.dumps(config)})
        self.set_mode(config["mode"])

    def set_mode(self, mode):
        if mode == "global":
            self.request("PUT", "/proxies/GLOBAL", {"name": ROOT_GROUP})


class Service:
    def __init__(self, store, core, proxy_url="http://127.0.0.1:7890"):
        self.store, self.core, self.proxy_url = store, core, proxy_url
        self.events = deque(maxlen=100)
        self.events_lock = threading.Lock()
        self.integrations = []
        self.report_time = 0
        self.last_subscription_attempt = 0
        self.record("info", "������������������")

    def record(self, level, message):
        with self.events_lock:
            self.events.append(
                {
                    "time": datetime.now(UTC).isoformat(),
                    "level": level,
                    "message": message,
                }
            )

    def event_snapshot(self):
        with self.events_lock:
            return list(self.events)

    def settings(self):
        with self.store.lock:
            data = dict(self.store.sources)
            data["has_lan_password"] = bool(data.pop("lan_password", ""))
            data["has_proxy_password"] = bool(data.pop("proxy_password", ""))
            data["outlet_source"] = source_choice(data)
            return data

    def status(self):
        version = self.core.request("GET", "/version")
        self.core.request("GET", "/proxies")["proxies"][ROOT_GROUP]
        with self.store.lock:
            sources = dict(self.store.sources)
            recovery = self.store.recovery_error
            config = self.store.runtime_config or self.store.config(sources)
            snapshot = dict(self.store.snapshot)
        current = source_choice(sources)
        outlets = [
            {
                "name": "subscription",
                "label": "��������",
                "ready": bool(sources.get("subscription_url") and snapshot),
            },
            {
                "name": "lan",
                "label": "��������������",
                "ready": bool(sources.get("lan_host")),
            },
        ]
        return {
            "version": version["version"],
            "current": current,
            "outlets": outlets,
            "options": [item["label"] for item in outlets if item["ready"]],
            "current_label": next(
                item["label"] for item in outlets if item["name"] == current
            ),
            "has_subscription": bool(sources.get("subscription_url")),
            "has_lan": bool(sources.get("lan_host")),
            "recovery_error": recovery,
            "configured": not recovery
            and any(item["name"] == current and item["ready"] for item in outlets),
            "traffic_scope": "explicit"
            if recovery
            else sources.get("traffic_mode", "explicit"),
            "core_mode": config["mode"],
            "share_enabled": False if recovery else sources.get("share_enabled", False),
            "subscription": {
                "fetched_at": snapshot.get("fetched_at"),
                "rules": len(config.get("rules", [])),
                "groups": max(0, len(config.get("proxy-groups", [])) - 1),
            },
            "traffic": self.store.traffic.status() if self.store.traffic else {},
            "integration_connected": time.monotonic() - self.report_time < 45,
        }

    def update(self, settings):
        if settings.get("traffic_mode") == "selected" or (
            self.store.sources.get("traffic_mode") == "selected"
            and "selected_integrations" in settings
        ):
            chosen = settings.get(
                "selected_integrations",
                self.store.sources.get("selected_integrations", []),
            )
            known = {item["id"] for item in self.integrations if not item["disabled"]}
            if chosen and (
                time.monotonic() - self.report_time >= 45 or not set(chosen) <= known
            ):
                raise ConfigError("integration_unavailable")
        self.store.update(settings)
        self.record("info", "������У�顢���沢�ȼ���")
        return {"saved": True}

    def select(self, name):
        aliases = {"��������": "subscription", "��������������": "lan"}
        name = aliases.get(name, name) if isinstance(name, str) else None
        if name not in ("subscription", "lan"):
            raise ValueError("Unknown outlet")
        self.store.update({"outlet_source": name})
        self.record("info", "���л�������Դ")
        return {"selected": name}

    def refresh(self):
        if not self.store.sources.get("subscription_url"):
            raise ValueError("No subscription configured")
        self.store.update({}, refresh=True)
        self.record("info", "������������ȡ��У�鲢����")
        return {"refreshed": True}

    def auto_refresh(self):
        with self.store.lock:
            if self.store.recovery_error:
                return
            url = self.store.sources.get("subscription_url")
            interval = self.store.sources.get("subscription_interval", 3600)
            fetched = self.store.snapshot.get("fetched_at", 0)
        now = time.time()
        if (
            not url
            or not interval
            or now - fetched < interval
            or now - self.last_subscription_attempt < 300
        ):
            return
        self.last_subscription_attempt = now
        try:
            self.refresh()
        except (OSError, ValueError, http.client.HTTPException):
            self.record(
                "warning", "�����Զ�����ʧ�ܣ����������ϴο������ã�����Ӻ�����"
            )

    def scan(self, cidr):
        results = asyncio.run(discover(cidr))
        self.record("info", f"����������ɨ����ɣ����� {len(results)} �����")
        return {"proxies": results}

    def delay(self, name):
        if name not in ("subscription", "lan") or name != source_choice(
            self.store.sources
        ):
            raise ValueError("Unknown active outlet")
        path = (
            "/proxies/"
            + quote(ROOT_GROUP, safe="")
            + "/delay?timeout=5000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204"
        )
        return self.core.request("GET", path)

    def test(
        self, test_url="https://www.gstatic.com/generate_204", *, ssl_context=None
    ):
        if self.store.recovery_error:
            raise ConfigError("startup_recovery")
        # Test target is fixed by the server, never supplied by browser input.
        parsed = urlsplit(self.proxy_url)
        user, password = (
            self.store.sources.get("proxy_username"),
            self.store.sources.get("proxy_password"),
        )
        proxy = f"{parsed.scheme}://{parsed.netloc}"
        opener = build_opener(
            ForcedProxyHandler({"http": proxy, "https": proxy}),
            HTTPSHandler(context=ssl_context),
        )
        request = Request(test_url)
        if user:
            credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
            request.add_header("Proxy-Authorization", f"Basic {credentials}")
        started = time.monotonic()
        with opener.open(request, timeout=15) as response:
            status = response.status
            response.read(1024)
        latency = round((time.monotonic() - started) * 1000)
        self.record("info", "����������ͨ�Բ��Գɹ�")
        return {
            "status": status,
            "latency_ms": latency,
            "url": test_url,
            "scope": "proxy_outlet_only",
        }

    def targets(self):
        traffic = self.store.traffic
        if traffic and not traffic.apps:
            traffic.refresh_inventory()
        return {
            "apps": traffic.apps if traffic else [],
            "integrations": list(self.integrations),
            "integration_connected": time.monotonic() - self.report_time < 45,
            "tun_available": traffic.capability() if traffic else False,
            "supervisor_available": traffic.inventory_ok if traffic else False,
            "addresses": {
                "controller_url": "http://127.0.0.1:9090",
                "manager_url": "http://127.0.0.1:9091",
                "proxy_url": "http://127.0.0.1:7890",
            },
            "lan_addresses": traffic.addresses if traffic else [],
        }

    def report(self, data):
        entries = data.get("integrations", [])
        if not isinstance(entries, list) or len(entries) > 256:
            raise ValueError("Invalid integration report")
        filtered = []
        for item in entries:
            if (
                not isinstance(item, dict)
                or any(
                    not isinstance(item.get(key), str) or len(item[key]) > 256
                    for key in ("id", "name", "domain")
                )
                or type(item.get("disabled")) is not bool
            ):
                raise ValueError("Invalid integration report")
            filtered.append(
                {key: item[key] for key in ("id", "name", "domain", "disabled")}
            )
        self.integrations = filtered
        self.report_time = time.monotonic()
        return {"reported": True}

    def runtime(self):
        # Loopback bearer API only. Never route this payload through Ingress.
        with self.store.lock:
            if self.store.recovery_error:
                # Existing integration hooks retain their policy on HTTP errors.
                raise ConfigError("startup_recovery")
            source = self.store.sources
            return {
                "traffic_mode": source.get("traffic_mode", "explicit"),
                "targets": source.get("selected_integrations", []),
                "proxy_url": self.proxy_url,
                "proxy_username": source.get("proxy_username", ""),
                "proxy_password": source.get("proxy_password", ""),
            }

    def diagnose(self):
        """Check saved HTTP authentication and two fixed HTTPS targets.

        Browser input cannot choose an address, target, or credentials. No raw
        exceptions, response bodies, URLs from settings, or secrets are returned.
        """
        results = []
        with self.store.lock:
            source = dict(self.store.sources)
        if source.get("lan_host") and source.get("lan_type", "http") == "http":
            connection = HTTPConnection(
                source["lan_host"], source.get("lan_port", 7890), timeout=8
            )
            response = None
            try:
                connection.putrequest("CONNECT", "example.com:443", skip_host=True)
                connection.putheader("Host", "example.com:443")
                if source.get("lan_username"):
                    credentials = base64.b64encode(
                        f"{source['lan_username']}:{source.get('lan_password', '')}".encode()
                    ).decode()
                    connection.putheader("Proxy-Authorization", f"Basic {credentials}")
                connection.endheaders()
                response = connection.getresponse()
                status = response.status
                result = {
                    "stage": "upstream_connect",
                    "target": "example.com:443",
                    "ok": status == 200,
                    "code": "ok"
                    if status == 200
                    else ("proxy_auth_required" if status == 407 else "http_error"),
                    "http_status": status,
                }
            except (OSError, http.client.HTTPException) as error:
                result = {
                    "stage": "upstream_connect",
                    "target": "example.com:443",
                    "ok": False,
                    "code": failure_code(error),
                }
            finally:
                if response is not None:
                    response.close()
                connection.close()
            results.append(result)
            if result["code"] == "proxy_auth_required":
                self.record("warning", "���δ�����֤��Ϸ��� HTTP 407")
                return {"checks": results, "scope": "proxy_outlet_only"}
        for target in ("https://www.gstatic.com/generate_204", "https://example.com/"):
            try:
                result = self.test(target)
                results.append(
                    {
                        "stage": "outlet_https",
                        "target": target,
                        "ok": True,
                        "code": "ok",
                        "http_status": result["status"],
                        "latency_ms": result["latency_ms"],
                    }
                )
            except (OSError, http.client.HTTPException) as error:
                results.append(
                    {
                        "stage": "outlet_https",
                        "target": target,
                        "ok": False,
                        "code": failure_code(error),
                    }
                )
        self.record("info", "���������ɣ�����������̶�Ŀ�꼰�������")
        return {"checks": results, "scope": "proxy_outlet_only"}
