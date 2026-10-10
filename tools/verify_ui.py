"""Headless browser QA against the app backend and real local Mihomo fixture."""

import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from core_fixture import Fixture
from test_manager import manager


def main():
    fixture = Fixture()
    server = None
    try:
        fixture.start()
        # Loopback allowance exists only in this test server; production is
        # restricted to the Supervisor Ingress peer at 172.30.32.2.
        prefix = "/api/hassio_ingress/fixture-session"
        parent = manager.handler_factory(
            fixture.store,
            fixture.secret,
            fixture.service,
            ingress=True,
            allowed_peer="127.0.0.1",
        )

        class GatewayFixture(parent):
            def request_path(self):
                path = super().request_path()
                return path.removeprefix(prefix) or "/"

            def do_GET(self):
                self.headers["X-Ingress-Path"] = prefix
                super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayFixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original_test = fixture.service.test
        fixture.service.test = lambda target="https://www.gstatic.com/generate_204": original_test(target, ssl_context=fixture.ssl_context)
        output = ROOT / "artifacts"
        output.mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1440, "height": 1000}, locale="zh-CN"
            )
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}{prefix}/")
            expect(page.locator("#core-state")).to_have_text("待配置代理源")
            page.locator("#lan-host").fill("127.0.0.1")
            page.locator("#lan-port").fill(str(fixture.http_port))
            page.locator("#lan-user").fill("lan-user")
            page.locator("#lan-password").fill("lan-password")
            page.get_by_role("button", name="保存代理源", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("代理源已保存")
            expect(page.locator("#current-node")).to_have_text("LAN")
            expect(page.locator("#lan-password")).to_have_value("")
            page.locator("#test-outlet").click()
            expect(page.locator("#notice")).to_contain_text("HTTP 200")

            page.get_by_role("button", name="诊断连接", exact=True).click()
            expect(page.locator("#diagnostic-results")).to_contain_text("上游代理握手 · example.com:443：成功")
            expect(page.locator("#diagnostic-results")).to_contain_text("https://example.com/：成功")
            expect(page.locator("#diagnostic-results")).not_to_contain_text("lan-password")

            page.locator("#direct-domains").fill("example.cn")
            page.locator("#proxy-domains").fill("example.com")
            page.get_by_role("button", name="保存分流规则", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("分流规则已保存")
            assert fixture.store.sources["lan_password"] == "lan-password"

            page.locator("#cidr").fill("8.8.8.0/24")
            page.get_by_role("button", name="发现代理", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("设置无效")
            # Positive discovery results are a UI fixture, not a LAN scan.
            fixture.service.scan = lambda _cidr: {
                "proxies": [f"http://127.0.0.1:{fixture.http_port}"]
            }
            page.locator("#cidr").fill("192.168.1.0/24")
            page.get_by_role("button", name="发现代理", exact=True).click()
            page.get_by_role("button", name="填入代理源", exact=True).click()
            expect(page.locator("#lan-host")).to_have_value("127.0.0.1")

            # Reload discards the unsaved discovery form and reads persisted settings.
            page.reload()
            expect(page.locator("#current-node")).to_have_text("LAN")
            expect(page.locator("#lan-user")).to_have_value("lan-user")
            page.locator("#subscription-url").fill(
                f"http://127.0.0.1:{fixture.subscription_port}/subscription"
            )
            page.get_by_role("button", name="保存代理源", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("代理源已保存")
            page.get_by_role("button", name="刷新状态", exact=True).click()
            page.get_by_role("button", name="使用 Fixture-B", exact=True).click()
            expect(page.locator("#current-node")).to_have_text("Fixture-B")
            page.get_by_role("button", name="更新订阅", exact=True).click()
            expect(page.locator("#notice")).to_have_text("订阅已更新。")
            page.locator("#test-outlet").click()
            expect(page.locator("#notice")).to_contain_text("HTTP 200")
            page.evaluate("location.hash = 'overview'")
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(output / "desktop.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.set_viewport_size({"width": 390, "height": 844})
            page.evaluate("window.scrollTo(0, 0)")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "mobile.png"))
            assert errors == [], errors
            browser.close()
        print(
            "UI PASS: Ingress path, source save, password retention, rules, outlet test, node switch, subscription refresh, discovery selection, desktop/mobile layout"
        )
    finally:
        if server:
            server.shutdown()
            server.server_close()
        fixture.close()


if __name__ == "__main__":
    main()
