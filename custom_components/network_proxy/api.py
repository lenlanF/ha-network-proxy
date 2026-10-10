from urllib.parse import quote

from aiohttp import ClientTimeout


class Controller:
    def __init__(self, session, url, secret):
        self.session, self.url, self.secret = session, url.rstrip("/"), secret

    async def request(self, method, path, data=None):
        async with self.session.request(
            method,
            self.url + path,
            headers={"Authorization": f"Bearer {self.secret}"},
            json=data,
            timeout=ClientTimeout(total=10),
        ) as response:
            response.raise_for_status()
            if response.status == 204:
                return None
            return await response.json()

    async def status(self):
        version = await self.request("GET", "/version")
        proxies = await self.request("GET", "/proxies")
        group = proxies["proxies"]["PROXY"]
        providers = await self.request("GET", "/providers/proxies")
        return {
            "version": version["version"],
            "options": group["all"],
            "current": group.get("now"),
            "has_subscription": "subscription" in providers.get("providers", {}),
        }

    async def select(self, name):
        await self.request("PUT", "/proxies/" + quote("PROXY", safe=""), {"name": name})

    async def refresh_subscription(self):
        await self.request("PUT", "/providers/proxies/subscription")


class Manager(Controller):
    async def health(self):
        return await self.request("GET", "/health")

    async def sources(self, sources):
        return await self.request("PUT", "/sources", sources)
