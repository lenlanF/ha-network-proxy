import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generate = load("generate", "addons/network_proxy/generate.py").generate
discover = load("discovery", "custom_components/network_proxy/discovery.py").discover


class ConfigTests(unittest.TestCase):
    def test_subscription_and_lan_share_group(self):
        config = generate(
            {
                "controller_secret": "x" * 24,
                "subscription_url": "https://example.com/sub",
                "lan_host": "192.168.1.2",
                "lan_type": "socks5",
                "lan_port": 1080,
            }
        )
        self.assertEqual(config["proxy-groups"][0]["use"], ["subscription"])
        self.assertIn("LAN", config["proxy-groups"][0]["proxies"])
        self.assertEqual(config["proxies"][0]["type"], "socks5")
        self.assertEqual(config["rules"][-1], "MATCH,PROXY")

    def test_reject_invalid_configuration(self):
        for options in (
            {},
            {"controller_secret": "x" * 24},
            {"controller_secret": "x" * 24, "subscription_url": "file:///etc/passwd"},
            {"controller_secret": "x" * 24, "lan_host": "192.168.1.2", "lan_port": 0},
        ):
            with self.assertRaises(ValueError):
                generate(options)

    def test_discovery_rejects_public_and_large_networks(self):
        for network in ("8.8.8.0/24", "192.168.0.0/16", "127.0.0.0/24", "fc00::/120"):
            with self.assertRaises(ValueError):
                asyncio.run(discover(network))

    def test_openclash_mixed_port_discovery_requires_proxy_handshake(self):
        async def check():
            async def serve(reader, writer):
                try:
                    prefix = await reader.readexactly(3)
                    if prefix == b"\x05\x01\x00":
                        writer.write(b"\x05\xff")
                    else:
                        await reader.readuntil(b"\r\n\r\n")
                        writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_server(serve, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            open_connection = asyncio.open_connection

            async def route(host, candidate):
                if host != "192.168.68.15" or candidate != 7893:
                    raise ConnectionRefusedError
                return await open_connection("127.0.0.1", port)

            async with server:
                for name, path in (
                    ("app_discovery_test", "addons/network_proxy/discovery.py"),
                    ("integration_discovery_test", "custom_components/network_proxy/discovery.py"),
                ):
                    scanner = load(name, path)
                    with patch("asyncio.open_connection", side_effect=route):
                        self.assertEqual(
                            await scanner.discover("192.168.68.15/32"),
                            ["http://192.168.68.15:7893"],
                        )

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
