"""Exercise real manager startup in an isolated disposable Docker container."""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def main():
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
        process = subprocess.Popen(["python", "/manager.py"], stdout=log, stderr=log)
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
        process = subprocess.Popen(["python", "/manager.py"], stdout=log, stderr=log)
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
    print(
        "STARTUP PASS: actual manager process; emoji subscription startup; private settings/cache retained; reject recovery; no capture/sharing; guarded integration sync; manual repair; secret-free logs",
        flush=True,
    )


if __name__ == "__main__":
    main()
