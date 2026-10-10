"""UI-facing operations, with no generic controller API passthrough."""

import asyncio
import base64
import json
import threading
import time
from collections import deque
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from discovery import discover


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


class Service:
    def __init__(self, store, core, proxy_url="http://127.0.0.1:7890"):
        self.store, self.core, self.proxy_url = store, core, proxy_url
        self.events = deque(maxlen=100)
        self.events_lock = threading.Lock()
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
            return data

    def status(self):
        version = self.core.request("GET", "/version")
        proxies = self.core.request("GET", "/proxies")["proxies"]
        group = proxies["PROXY"]
        nodes = []
        for name in group["all"]:
            proxy = proxies.get(name, {})
            history = proxy.get("history", [])
            nodes.append(
                {
                    "name": name,
                    "type": proxy.get("type", "unknown"),
                    "delay": history[-1].get("delay") if history else None,
                }
            )
        return {
            "version": version["version"],
            "current": group.get("now"),
            "nodes": nodes,
            "has_subscription": bool(self.store.sources.get("subscription_url")),
            "has_lan": bool(self.store.sources.get("lan_host")),
            "configured": bool(
                self.store.sources.get("subscription_url")
                or self.store.sources.get("lan_host")
            ),
            "traffic_scope": "explicit_proxy_only",
        }

    def update(self, settings):
        self.store.update(settings)
        self.record("info", "代理源与分流设置已保存并热加载")
        return {"saved": True}

    def select(self, name):
        with self.store.lock:
            group = self.core.request("GET", "/proxies/PROXY")
            if not isinstance(name, str) or name not in group["all"]:
                raise ValueError("Unknown node")
            self.core.request("PUT", "/proxies/PROXY", {"name": name})
        self.record("info", "已切换代理节点")
        return {"selected": name}

    def refresh(self):
        with self.store.lock:
            if not self.store.sources.get("subscription_url"):
                raise ValueError("No subscription configured")
            self.core.request("PUT", "/providers/proxies/subscription")
        self.record("info", "已触发订阅更新")
        return {"refreshed": True}

    def scan(self, cidr):
        results = asyncio.run(discover(cidr))
        self.record("info", f"局域网代理扫描完成，发现 {len(results)} 个入口")
        return {"proxies": results}

    def delay(self, name):
        group = self.core.request("GET", "/proxies/PROXY")
        if not isinstance(name, str) or name not in group["all"] or name == "REJECT":
            raise ValueError("Unknown node")
        path = (
            "/proxies/"
            + quote(name, safe="")
            + "/delay?timeout=5000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204"
        )
        return self.core.request("GET", path)

    def test(
        self, test_url="https://www.gstatic.com/generate_204", *, ssl_context=None
    ):
        # Test target is fixed by the server, never supplied by browser input.
        parsed = urlsplit(self.proxy_url)
        user, password = (
            self.store.options.get("proxy_username"),
            self.store.options.get("proxy_password"),
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
        self.record("info", "代理出口连通性测试成功")
        return {
            "status": status,
            "latency_ms": latency,
            "url": test_url,
            "scope": "proxy_outlet_only",
        }
