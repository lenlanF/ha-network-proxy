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

import yaml
from discovery import discover
from generate import ROOT_GROUP, source_choice
from profiles import selected_document
from subscription import ConfigError, compose


def failure_code(error):
    """Return only allowlisted reasons; exception text may contain credentials."""
    if isinstance(error, HTTPError):
        return "proxy_auth_required" if error.code == 407 else "http_error"
    if isinstance(error, URLError):
        error = error.reason
    if isinstance(error, ssl.SSLCertVerificationError):
        return "tls_certificate_error"
    if isinstance(error, ssl.SSLEOFError):
        return "tls_connection_closed"
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
        self.request(
            "PUT",
            "/configs?force=true",
            {"payload": json.dumps(config, ensure_ascii=False)},
        )
        self.set_mode(config["mode"])
        if not config.get("mixed-port"):
            self.request("DELETE", "/connections")

    def restore_selections(self, config, selections):
        proxies = self.request("GET", "/proxies")["proxies"]
        for group in config.get("proxy-groups", []) + [{"name": "GLOBAL"}]:
            name = group["name"]
            current = proxies.get(name, {})
            children = current.get("all", [])
            if name == ROOT_GROUP or not children:
                continue
            chosen = selections.get(name)
            if (
                current.get("type") in ("URLTest", "Fallback", "LoadBalance")
                and chosen not in children
            ):
                self.request("DELETE", "/proxies/" + quote(name, safe=""))
                continue
            if chosen not in children:
                chosen = (
                    children[0]
                    if current.get("type") == "Selector" and name != "GLOBAL"
                    else None
                )
            if chosen and current.get("now") != chosen:
                self.request(
                    "PUT", "/proxies/" + quote(name, safe=""), {"name": chosen}
                )

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
        self.last_subscription_attempt = {}
        self.record("info", "代理管理服务已启动")

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
        enabled = sources.get("proxy_enabled", True)
        outlets = [
            {
                "name": "subscription",
                "label": "订阅配置",
                "ready": bool(sources.get("subscription_url") and snapshot),
            },
            {
                "name": "lan",
                "label": "局域网共享代理",
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
            "proxy_enabled": enabled,
            "active_subscription": sources.get("active_subscription", ""),
            "configured": not recovery
            and any(item["name"] == current and item["ready"] for item in outlets),
            "traffic_scope": "explicit"
            if recovery or not enabled
            else sources.get("traffic_mode", "explicit"),
            "core_mode": config["mode"],
            "core_mode_setting": sources.get("core_mode", "native"),
            "share_enabled": False
            if recovery or not enabled
            else sources.get("share_enabled", False),
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
        self.record("info", "配置已校验、保存并热加载")
        return {"saved": True}

    def select(self, name):
        aliases = {"订阅配置": "subscription", "局域网共享代理": "lan"}
        name = aliases.get(name, name) if isinstance(name, str) else None
        if name not in ("subscription", "lan"):
            raise ValueError("Unknown outlet")
        self.store.update({"outlet_source": name})
        self.record("info", "已切换代理来源")
        return {"selected": name}

    def refresh(self):
        if not self.store.sources.get("subscription_url"):
            raise ValueError("No subscription configured")
        self.store.update({}, refresh=True)
        self.record("info", "订阅已重新拉取、校验并加载")
        return {"refreshed": True}

    def subscriptions(self):
        with self.store.lock:
            active = self.store.sources.get("active_subscription")
            return {
                "active_id": active,
                "profiles": [
                    {
                        **profile,
                        "active": profile["id"] == active,
                        "fetched_at": self.store.snapshots.get(profile["id"], {}).get(
                            "fetched_at"
                        ),
                        "ready": bool(
                            self.store.snapshots.get(profile["id"], {}).get("document")
                        ),
                    }
                    for profile in self.store.sources.get("subscriptions", [])
                ],
            }

    def subscription_action(self, operation, data):
        identity = data.get("id")
        if operation == "save":
            identity = self.store.save_subscription(data)
        elif operation == "delete":
            self.store.delete_subscription(identity)
        elif operation == "activate":
            if not any(
                item["id"] == identity
                for item in self.store.sources.get("subscriptions", [])
            ):
                raise ConfigError("profile_not_found")
            self.store.update(
                {"active_subscription": identity, "outlet_source": "subscription"}
            )
        elif operation == "refresh":
            if not any(
                item["id"] == identity
                for item in self.store.sources.get("subscriptions", [])
            ):
                raise ConfigError("profile_not_found")
            self.store.update({}, refresh_ids=[identity])
        elif operation == "edit":
            self.store.edit_subscription(
                identity, data.get("yaml", ""), native=data.get("native") is True
            )
        else:
            raise ValueError("Unknown operation")
        self.record("info", "订阅配置已校验并保存")
        return {"saved": True, "id": identity}

    def subscription_config(self, identity, view):
        with self.store.lock:
            profile = next(
                (
                    item
                    for item in self.store.sources.get("subscriptions", [])
                    if item["id"] == identity
                ),
                None,
            )
            snapshot = self.store.snapshots.get(identity, {})
            if profile is None or not snapshot.get("document"):
                raise ConfigError("profile_not_ready")
            if view == "original":
                text = snapshot.get("original_yaml") or yaml.safe_dump(
                    dict(snapshot["document"]), allow_unicode=True, sort_keys=False
                )
            elif view == "editable":
                text = (
                    snapshot.get("edited_yaml")
                    or snapshot.get("original_yaml")
                    or yaml.safe_dump(
                        dict(snapshot["document"]), allow_unicode=True, sort_keys=False
                    )
                )
            elif view == "effective":
                # Excludes application controller/outlet secrets and network listeners.
                document = selected_document(profile, snapshot)
                text = yaml.safe_dump(
                    compose(
                        document, profile.get("extension_yaml", ""), namespace=identity
                    ),
                    allow_unicode=True,
                    sort_keys=False,
                )
            else:
                raise ValueError("Unknown view")
            return {
                "id": identity,
                "view": view,
                "yaml": text,
                "config_mode": profile.get("config_mode", "native"),
            }

    def policies(self):
        raw = self.core.request("GET", "/proxies")["proxies"]
        groups, nodes = [], []
        for name, item in raw.items():
            if name == ROOT_GROUP:
                continue
            history = item.get("history") or []
            record = {
                "name": name,
                "type": item.get("type", ""),
                "alive": item.get("alive"),
                "delay": history[-1].get("delay") if history else None,
            }
            if "all" in item:
                groups.append(
                    {
                        **record,
                        "current": item.get("now", ""),
                        "members": item["all"],
                        "selectable": item.get("type")
                        in ("Selector", "URLTest", "Fallback", "LoadBalance"),
                        "automatic": item.get("type") != "Selector",
                    }
                )
            else:
                nodes.append(record)
        return {"groups": groups, "nodes": nodes}

    def select_policy(self, group, name, *, automatic=False):
        with self.store.lock:
            raw = self.core.request("GET", "/proxies")["proxies"]
            item = raw.get(group, {}) if isinstance(group, str) else {}
            if group == ROOT_GROUP or item.get("type") not in (
                "Selector",
                "URLTest",
                "Fallback",
                "LoadBalance",
            ):
                raise ConfigError("group_not_selectable")
            path = "/proxies/" + quote(group, safe="")
            if automatic and item.get("type") != "Selector":
                self.core.request("DELETE", path)
                try:
                    self.store.save_selection(group, "")
                except Exception:
                    if item.get("now"):
                        self.core.request("PUT", path, {"name": item["now"]})
                    raise
            elif isinstance(name, str) and name in item.get("all", []):
                self.core.request("PUT", path, {"name": name})
                try:
                    self.store.save_selection(group, name)
                except Exception:
                    if item.get("now"):
                        self.core.request("PUT", path, {"name": item["now"]})
                    raise
            else:
                raise ConfigError("invalid_group_member")
            self.record("info", "策略组选择已更新")
            return {"selected": True}

    def auto_refresh(self):
        with self.store.lock:
            if self.store.recovery_error:
                return
            profiles = list(self.store.sources.get("subscriptions", []))
        now = time.time()
        for profile in profiles:
            identity = profile["id"]
            interval = profile.get("interval", 3600)
            with self.store.lock:
                fetched = self.store.snapshots.get(identity, {}).get("fetched_at", 0)
            if (
                not interval
                or now - fetched < interval
                or now - self.last_subscription_attempt.get(identity, 0) < 300
            ):
                continue
            self.last_subscription_attempt[identity] = now
            try:
                self.store.update({}, refresh_ids=[identity])
                self.record("info", "订阅自动更新成功；本地编辑副本已保留")
            except (OSError, ValueError, http.client.HTTPException):
                self.record(
                    "warning", "订阅自动更新失败，保留上次可用配置；五分钟后重试"
                )

    def scan(self, cidr):
        results = asyncio.run(discover(cidr))
        self.record("info", f"局域网代理扫描完成，发现 {len(results)} 个入口")
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
        self,
        test_url="https://www.gstatic.com/generate_204",
        *,
        ssl_context=None,
        proxy_url=None,
        auth=None,
    ):
        if self.store.recovery_error:
            raise ConfigError("startup_recovery")
        if proxy_url is None and not self.store.sources.get("proxy_enabled", True):
            raise ConfigError("proxy_disabled")
        # Test target is fixed by the server, never supplied by browser input.
        parsed = urlsplit(proxy_url or self.proxy_url)
        user, password = (
            self.store.sources.get("proxy_username"),
            self.store.sources.get("proxy_password"),
        )
        if auth is not None:
            user, password = auth
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
        self.record("info", "代理出口连通性测试成功")
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
            enabled = source.get("proxy_enabled", True)
            return {
                "proxy_enabled": enabled,
                "traffic_mode": source.get("traffic_mode", "explicit")
                if enabled
                else "explicit",
                "targets": source.get("selected_integrations", []) if enabled else [],
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
            if (
                result["code"] == "proxy_auth_required"
                and source_choice(source) == "lan"
            ):
                self.record("warning", "上游代理认证诊断返回 HTTP 407")
                return {"checks": results, "scope": "proxy_outlet_only"}
        for target in ("https://www.gstatic.com/generate_204", "https://example.com/"):
            if not source.get("proxy_enabled", True):
                results.append(
                    {
                        "stage": "outlet_https",
                        "target": target,
                        "ok": False,
                        "code": "proxy_disabled",
                    }
                )
                continue
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
        self.record("info", "代理诊断完成，结果仅包含固定目标及错误分类")
        return {
            "checks": results,
            "scope": "proxy_outlet_only",
            "active_outlet": source_choice(source),
        }
