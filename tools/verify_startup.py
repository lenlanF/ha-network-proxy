"""Exercise real manager startup in an isolated disposable Docker container."""

import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def verify_startup(ingress_port, environment):
    if os.environ.get("NP_ISOLATED_TEST") != "1":
        raise SystemExit("Run only in an isolated disposable Docker container")
    directory = Path("/data")
    directory.mkdir(mode=0o700)
    secret = "isolated-startup-fixture-secret"
    url = "https://invalid.example/fixture"
    document = {
        "proxies": [
            {"name": "A", "type": "http", "server": "127.0.0.1", "port": 18888}
        ],
        "proxy-groups": [{"name": "Broken", "type": "select"}],
        "rules": ["MATCH,Broken"],
    }
    snapshot = {
        "url_hash": hashlib.sha256(url.encode()).hexdigest(),
        "document": document,
        "fetched_at": 1,
    }
    sources = {
        "subscription_url": url,
        "outlet_source": "subscription",
        "share_enabled": True,
    }
    for name, value in (
        ("options.json", {"controller_secret": secret}),
        ("sources.json", sources),
        ("subscription.json", snapshot),
    ):
        (directory / name).write_text(json.dumps(value))
        (directory / name).chmod(0o600)
    originals = {
        name: (directory / name).read_bytes()
        for name in ("sources.json", "subscription.json")
    }
    opener = build_opener(ProxyHandler({}))

    def request(path, method="GET", data=None):
        req = Request(
            "http://127.0.0.1:9091" + path,
            method=method,
            headers={
                "Authorization": "Bearer " + secret,
                "Content-Type": "application/json",
            },
            data=json.dumps(data).encode() if data is not None else None,
        )
        with opener.open(req, timeout=3) as response:
            return json.load(response)

    with (directory / "startup.log").open("w+") as log:
        process = subprocess.Popen(
            ["python", "/manager.py"], stdout=log, stderr=log, env=environment
        )
        try:
            for _ in range(100):
                assert process.poll() is None, "Manager exited during recovery startup"
                try:
                    status = request("/api/status")
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("Recovery management API did not start")
            assert status["recovery_error"] == "core_validation_proxy_group", status
            assert not status["configured"] and status["traffic_scope"] == "explicit"
            assert not status["share_enabled"]
            try:
                opener.open(f"http://127.0.0.1:{ingress_port}/api/status", timeout=3)
            except HTTPError as error:
                assert error.code == 403 and json.load(error)["error"] == "ingress_only"
            else:
                raise AssertionError(
                    "Dynamic Ingress must still reject non-Supervisor peers"
                )
            assert all(
                (directory / name).read_bytes() == value
                for name, value in originals.items()
            )
            assert not (directory / "traffic-state.json").exists()
            active = json.loads((directory / "config.json").read_text())
            assert active["proxy-groups"][0]["proxies"] == ["REJECT"]
            assert active["bind-address"] == "127.0.0.1" and not active["tun"]["enable"]
            try:
                request("/integration/runtime")
            except HTTPError as error:
                assert json.load(error)["error"] == "startup_recovery"
            else:
                raise AssertionError(
                    "Recovery must not clear existing integration policies"
                )
            request(
                "/api/settings",
                "PUT",
                {
                    "subscription_url": "",
                    "outlet_source": "lan",
                    "lan_host": "127.0.0.1",
                    "lan_port": 18888,
                },
            )
            status = request("/api/status")
            assert not status["recovery_error"] and status["configured"]
            assert status["traffic_scope"] == "explicit" and not status["share_enabled"]
            assert request("/integration/runtime")["traffic_mode"] == "explicit"
        finally:
            process.terminate()
            process.wait(timeout=10)
        log.seek(0)
        output = log.read()
        assert secret not in output and url not in output
        assert "startup recovery: core_validation_proxy_group" in output
    # Restore an ASCII-escaped legacy snapshot with non-BMP names, then boot
    # the actual manager and core. Persisted JSON is decoded by json.load;
    # only the newly generated core file must avoid surrogate-pair escapes.
    node, group = "🇭🇰香港节点🚀", "🌍订阅策略"
    document["proxies"][0]["name"] = node
    document["proxy-groups"] = [{"name": group, "type": "select", "proxies": [node]}]
    document["rules"] = [f"MATCH,{group}"]
    snapshot["document"] = document
    sources.update(traffic_mode="explicit", share_enabled=False)
    for name, value in (("sources.json", sources), ("subscription.json", snapshot)):
        (directory / name).write_text(json.dumps(value), encoding="utf-8")
    with (directory / "emoji-startup.log").open("w+") as log:
        process = subprocess.Popen(
            ["python", "/manager.py"], stdout=log, stderr=log, env=environment
        )
        try:
            for _ in range(100):
                assert process.poll() is None, "Manager exited during emoji startup"
                try:
                    status = request("/api/status")
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("Emoji subscription did not start")
            assert status["configured"] and not status["recovery_error"], status
            active = json.loads((directory / "config.json").read_text(encoding="utf-8"))
            assert active["proxies"][0]["name"] == node
            assert active["proxy-groups"][0]["name"] == group
        finally:
            process.terminate()
            process.wait(timeout=10)
        log.seek(0)
        output = log.read()
        assert secret not in output and url not in output
    # A conflict on the fixed integration API must be identified before the
    # core is launched or any transparent route is activated.
    with socket.socket() as occupied_api:
        occupied_api.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied_api.bind(("127.0.0.1", 9091))
        occupied_api.listen()
        with (directory / "busy-api.log").open("w+") as log:
            process = subprocess.Popen(
                ["python", "/manager.py"], stdout=log, stderr=log, env=environment
            )
            assert process.wait(timeout=15) != 0
            log.seek(0)
            output = log.read()
            assert "management_port_in_use:9091" in output, output
            assert "Start initial configuration" not in output
            assert secret not in output and url not in output
            assert not (directory / "traffic-state.json").exists()
            assert occupied_api.getsockname()[1] == 9091
    print(
        "STARTUP PASS: actual manager process; occupied 8099 bypassed with Supervisor-assigned Ingress; Ingress peer guard retained; busy API detected before core/routes; emoji subscription startup; private settings/cache retained; reject recovery; manual repair; secret-free logs",
        flush=True,
    )


def main():
    if os.environ.get("NP_ISOLATED_TEST") != "1":
        raise SystemExit("Run only in an isolated disposable Docker container")
    token = "isolated-supervisor-fixture-token"
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        ingress_port = unused.getsockname()[1]

    class SupervisorAPI(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer " + token
            data = {
                "/addons/self/info": {"ingress_port": ingress_port},
                "/supervisor/info": {"addons": []},
                "/network/info": {"interfaces": []},
            }.get(self.path, {})
            body = json.dumps({"result": "ok", "data": data}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with Path("/etc/hosts").open("a") as hosts:
        hosts.write("\n127.0.0.1 supervisor\n")
    server = ThreadingHTTPServer(("127.0.0.1", 80), SupervisorAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket() as occupied_ingress:
            occupied_ingress.bind(("0.0.0.0", 8099))
            occupied_ingress.listen()
            verify_startup(ingress_port, {**os.environ, "SUPERVISOR_TOKEN": token})
            assert occupied_ingress.getsockname()[1] == 8099
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
