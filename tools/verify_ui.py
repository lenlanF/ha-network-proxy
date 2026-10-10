"""Headless browser QA against the app backend and real local Mihomo fixture."""

import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

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
        from traffic import Traffic

        fixture.store.traffic = Traffic(
            fixture.directory,
            SimpleNamespace(
                inventory=lambda: [
                    {
                        "id": "demo_app",
                        "name": "ʾ��Ӧ�ã�UI ���ԣ�",
                        "state": "started",
                        "supported": True,
                        "ip": "172.30.33.10",
                        "reason": "",
                    },
                    {
                        "id": "host_app",
                        "name": "��������Ӧ�ã�UI ���ԣ�",
                        "state": "started",
                        "supported": False,
                        "ip": None,
                        "reason": "host_network",
                    },
                ],
                lan_addresses=lambda: ["192.168.1.2"],
            ),
        )
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
        fixture.service.test = lambda target="https://www.gstatic.com/generate_204": (
            original_test(target, ssl_context=fixture.ssl_context)
        )
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
            expect(page.locator("#core-state")).to_have_text("�����ô���Դ")
            page.locator("#lan-host").fill("127.0.0.1")
            page.locator("#lan-port").fill(str(fixture.http_port))
            page.locator("#lan-user").fill("lan-user")
            page.locator("#lan-password").fill("lan-password")
            page.locator("#outlet-source").select_option("lan")
            page.get_by_role("button", name="�����������", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("������������ȡ")
            expect(page.locator("#current-node")).to_have_text("��������������")
            expect(page.locator("#lan-password")).to_have_value("")
            page.locator("#test-outlet").click()
            expect(page.locator("#notice")).to_contain_text("HTTP 200")

            page.get_by_role("button", name="�������", exact=True).click()
            expect(page.locator("#diagnostic-results")).to_contain_text(
                "���δ������� �� example.com:443���ɹ�"
            )
            expect(page.locator("#diagnostic-results")).to_contain_text(
                "https://example.com/���ɹ�"
            )
            expect(page.locator("#diagnostic-results")).not_to_contain_text(
                "lan-password"
            )

            page.locator("#legacy-rules summary").click()
            page.locator("#direct-domains").fill("example.cn")
            page.locator("#proxy-domains").fill("example.com")
            page.get_by_role("button", name="������׹���", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("���׹����ѱ���")
            assert fixture.store.sources["lan_password"] == "lan-password"

            page.locator("#cidr").fill("8.8.8.0/24")
            page.get_by_role("button", name="���ִ���", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("������Ч")
            # Positive discovery results are a UI fixture, not a LAN scan.
            fixture.service.scan = lambda _cidr: {
                "proxies": [f"http://127.0.0.1:{fixture.http_port}"]
            }
            page.locator("#cidr").fill("192.168.1.0/24")
            page.get_by_role("button", name="���ִ���", exact=True).click()
            page.get_by_role("button", name="�����������", exact=True).click()
            expect(page.locator("#lan-host")).to_have_value("127.0.0.1")

            # Reload discards the unsaved discovery form and reads persisted settings.
            page.reload()
            expect(page.locator("#current-node")).to_have_text("��������������")
            expect(page.locator("#lan-user")).to_have_value("lan-user")
            page.locator("#subscription-url").fill(
                f"http://127.0.0.1:{fixture.subscription_port}/subscription"
            )
            page.locator("#outlet-source").select_option("subscription")
            page.locator("#extension-yaml").fill(
                "append-rules:\n  - DOMAIN,override.test,FixturePolicy"
            )
            page.get_by_role("button", name="�����������", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("������������ȡ")
            page.get_by_role("button", name="ˢ��״̬", exact=True).click()
            expect(page.locator("#current-node")).to_have_text("��������")
            expect(page.locator("#nodes .node")).to_have_count(2)
            expect(page.locator("#nodes")).not_to_contain_text("Fixture-B")
            page.get_by_role("button", name="ʹ�� ��������������", exact=True).click()
            expect(page.locator("#current-node")).to_have_text("��������������")
            page.get_by_role("button", name="ʹ�� ��������", exact=True).click()
            expect(page.locator("#notice")).to_have_text("���л����������á�")
            page.get_by_role("button", name="���¶���", exact=True).click()
            expect(page.locator("#notice")).to_have_text("������������ȡ�����ء�")
            page.locator("#test-outlet").click()
            expect(page.locator("#notice")).to_contain_text("HTTP 200")
            page.locator("#share-enabled").check()
            page.get_by_role("button", name="���湲������", exact=True).click()
            expect(page.locator("#notice")).to_have_text("���������ѱ��档")
            assert fixture.store.sources["share_enabled"]
            expect(page.locator("#copy-manager_url")).to_have_value(
                "http://127.0.0.1:9091"
            )
            page.get_by_role("button", name="����Ӧ�ù�����ַ", exact=True).click()
            expect(page.locator("#notice")).to_have_text("�ڲ���ַ�Ѹ��ơ�")
            page.locator("#traffic-mode").select_option("selected")
            expect(page.locator("#integration-targets")).to_contain_text("��������")
            page.locator("#traffic-mode").select_option("haos")
            page.get_by_role("button", name="��������ģʽ", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("͸������������")
            assert fixture.store.sources.get("traffic_mode", "explicit") == "explicit"
            page.locator("#traffic-mode").select_option("explicit")
            page.locator("#core-mode").select_option("global")
            page.get_by_role("button", name="��������ģʽ", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("����ģʽ�ѱ���")
            fixture.service.report(
                {
                    "integrations": [
                        {
                            "id": "demo_integration",
                            "name": "������UI ���ԣ�",
                            "domain": "weather",
                            "disabled": False,
                        }
                    ]
                }
            )
            page.get_by_role("button", name="ˢ��״̬", exact=True).click()
            expect(page.locator("#integration-state")).to_contain_text("���׼���������")
            expect(
                page.locator('#app-targets input[value="host_app"]')
            ).to_be_disabled()
            page.locator("#traffic-mode").select_option("selected")
            page.locator('#integration-targets input[value="demo_integration"]').check()
            page.get_by_role("button", name="��������ģʽ", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("����ģʽ�ѱ���")
            expect(page.locator("#traffic-state")).to_have_text("����Ӧ�� / ����")
            assert fixture.store.sources["selected_integrations"] == [
                "demo_integration"
            ]
            page.locator("#sources").screenshot(
                path=str(output / "source-settings.png")
            )
            page.locator("#traffic").screenshot(
                path=str(output / "traffic-settings.png")
            )
            page.locator("#connection").screenshot(
                path=str(output / "connection-addresses.png")
            )
            page.evaluate("location.hash = 'overview'")
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(output / "desktop.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.set_viewport_size({"width": 390, "height": 844})
            page.evaluate("window.scrollTo(0, 0)")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "mobile.png"))
            fixture.store.sources["extension_yaml"] = (
                "proxy-groups:\n  - name: Broken\n    type: select"
            )
            fixture.store.prepare_or_recover()
            fixture.store.reload_config(fixture.store.runtime_config)
            page.reload()
            expect(page.locator("#core-state")).to_have_text("���ûָ���")
            expect(page.locator("#recovery-note")).to_be_visible()
            expect(page.locator("#recovery-note")).to_contain_text("������У��ʧ��")
            expect(page.locator("#current-node")).to_have_text("δ����")
            page.screenshot(path=str(output / "startup-recovery-mobile.png"))
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.screenshot(path=str(output / "startup-recovery.png"))
            page.locator("#extension-yaml").fill("")
            page.get_by_role("button", name="�����������", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("������������ȡ")
            expect(page.locator("#recovery-note")).to_be_hidden()
            expect(page.locator("#current-node")).to_have_text("��������")
            expect(page.locator("#traffic-state")).to_have_text("��ʽ����")
            assert not fixture.store.sources["share_enabled"]
            assert errors == [], errors
            browser.close()
        print(
            "UI PASS: Ingress, subscription + YAML save, password retention, rules, diagnostics, two source outlets, LAN sharing, copy addresses, modes, TUN refusal on Windows, desktop/mobile, startup recovery and manual repair"
        )
    finally:
        if server:
            server.shutdown()
            server.server_close()
        fixture.close()


if __name__ == "__main__":
    main()
