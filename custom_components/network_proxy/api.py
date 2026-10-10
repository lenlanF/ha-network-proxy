from aiohttp import ClientTimeout


class Controller:
    mutation_timeout = 10

    def __init__(self, session, url, secret):
        self.session, self.url, self.secret = session, url.rstrip("/"), secret

    async def request(self, method, path, data=None):
        async with self.session.request(
            method,
            self.url + path,
            headers={"Authorization": f"Bearer {self.secret}"},
            json=data,
            timeout=ClientTimeout(
                total=self.mutation_timeout if method in ("POST", "PUT") else 10
            ),
        ) as response:
            response.raise_for_status()
            if response.status == 204:
                return None
            return await response.json()

    async def status(self):
        return await self.request("GET", "/version")


class Manager(Controller):
    mutation_timeout = 120

    async def status(self):
        result = await self.request("GET", "/api/status")
        result["current"] = result["current_label"] if result["configured"] else None
        return result

    async def health(self):
        return await self.request("GET", "/health")

    async def sources(self, sources):
        return await self.request("PUT", "/sources", sources)

    async def select(self, name):
        return await self.request("POST", "/api/select", {"name": name})

    async def refresh_subscription(self):
        return await self.request("POST", "/api/refresh", {})

    async def runtime(self):
        return await self.request("GET", "/integration/runtime")

    async def report(self, entries):
        return await self.request(
            "POST", "/integration/report", {"integrations": entries}
        )
