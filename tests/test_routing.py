import asyncio
import unittest
from contextvars import ContextVar
from types import SimpleNamespace

from aiohttp import BasicAuth, ClientError, ClientSession, web
from test_core import load

routing = load("routing", "custom_components/network_proxy/routing.py")


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.context = ContextVar("entry", default=None)
        self.original = ClientSession._request
        self.router = routing.Router(self.context.get)
        self.paths = []

        async def proxy(request):
            self.paths.append(request.raw_path)
            return web.Response(text="via-proxy")

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", proxy)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.port = self.site._server.sockets[0].getsockname()[1]
        self.policy = routing.Policy(
            {"selected"}, f"http://127.0.0.1:{self.port}", BasicAuth("u", "p")
        )
        self.router.add("owner", self.policy)

    async def asyncTearDown(self):
        self.router.remove("owner")
        await self.runner.cleanup()
        self.assertIs(ClientSession._request, self.original)

    async def test_selected_public_request_reaches_real_proxy(self):
        self.context.set(SimpleNamespace(entry_id="selected"))
        async with (
            ClientSession() as session,
            session.get("http://example.com/resource") as response,
        ):
            self.assertEqual(await response.text(), "via-proxy")
        self.assertEqual(self.paths, ["http://example.com/resource"])
        self.assertEqual(self.policy.attempts["selected"], 1)
        self.assertEqual(self.policy.responses["selected"], 1)

    async def test_no_context_unselected_and_local_requests_remain_direct(self):
        async with ClientSession() as session:
            for entry in (
                None,
                SimpleNamespace(entry_id="other"),
                SimpleNamespace(entry_id="selected"),
            ):
                self.context.set(entry)
                async with session.get(
                    f"http://127.0.0.1:{self.port}/local"
                ) as response:
                    await response.read()
        self.assertEqual(self.paths, ["/local"] * 3)
        self.assertEqual(sum(self.policy.attempts.values()), 0)
        self.assertEqual(self.policy.skipped["selected"], 1)

    async def test_explicit_proxy_is_preserved(self):
        self.context.set(SimpleNamespace(entry_id="selected"))
        async with (
            ClientSession() as session,
            session.get(
                f"http://127.0.0.1:{self.port}/explicit", proxy=None
            ) as response,
        ):
            await response.read()
        self.assertEqual(self.paths, ["/explicit"])
        self.assertEqual(self.policy.skipped["selected"], 1)

    async def test_task_contexts_are_isolated(self):
        async with ClientSession() as session:

            async def request(entry_id):
                self.context.set(SimpleNamespace(entry_id=entry_id))
                url = (
                    "http://example.com/selected"
                    if entry_id == "selected"
                    else f"http://127.0.0.1:{self.port}/other"
                )
                async with session.get(url) as response:
                    await response.read()

            await asyncio.gather(request("selected"), request("other"))
        self.assertCountEqual(self.paths, ["http://example.com/selected", "/other"])
        self.assertEqual(self.policy.attempts["selected"], 1)

    async def test_proxy_failure_does_not_fallback_to_direct(self):
        self.context.set(SimpleNamespace(entry_id="selected"))
        self.policy.url = "http://127.0.0.1:0"
        async with ClientSession() as session:
            with self.assertRaises(ClientError):
                await session.get("http://example.com/")
        self.assertEqual(self.policy.failures["selected"], 1)
        self.assertEqual(self.paths, [])

    async def test_conflicting_target_and_unload(self):
        with self.assertRaises(ValueError):
            self.router.add(
                "other", routing.Policy({"selected"}, "http://127.0.0.1:7890")
            )
        self.router.remove("owner")
        self.assertIs(ClientSession._request, self.original)

    def test_local_url_classification(self):
        for url in (
            "http://localhost",
            "http://192.168.1.2",
            "http://device.local",
            "http://[::1]",
            "http://supervisor",
        ):
            self.assertTrue(routing.local_url(url), url)
        self.assertFalse(routing.local_url("https://example.com"))
        self.assertFalse(routing.local_url("https://1.1.1.1"))
