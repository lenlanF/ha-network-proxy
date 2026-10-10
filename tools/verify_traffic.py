"""Real TUN tests inside a disposable --privileged --network=none container.

Never run on the HA host. Namespaces, virtual links and firewall policies belong
only to the throwaway test container. No live Internet or credentials are used.
"""

import ipaddress
import json
import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
from urllib.request import ProxyHandler, build_opener

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "addons" / "network_proxy")
)
from generate import generate
from manager import SettingsStore
from service import CoreClient
from traffic import Traffic

TARGET = "203.0.113.8"
TARGET6 = "2001:db8:203::8"
UPSTREAM = "192.168.200.2"


def run(*args):
    result = subprocess.run(list(args), capture_output=True, timeout=20, check=False)
    if result.returncode:
        print(result.stderr.decode(), flush=True)
        result.check_returncode()
    return result.stdout.decode().strip()


def proxied(address):
    ip = ipaddress.ip_address(address)
    return (ip.ipv4_mapped or ip if ip.version == 6 else ip).is_loopback


def receive(stream, size):
    result = b""
    while len(result) < size:
        data = stream.read(size - len(result))
        if not data:
            raise EOFError
        result += data
    return result


def socks_address(stream):
    kind = receive(stream, 1)
    if kind == b"\x01":
        address = receive(stream, 4)
    elif kind == b"\x04":
        address = receive(stream, 16)
    elif kind == b"\x03":
        size = receive(stream, 1)
        address = size + receive(stream, size[0])
    else:
        raise ValueError("Invalid SOCKS address")
    port = receive(stream, 2)
    return kind + address + port, int.from_bytes(port, "big")


def relay(left, right):
    with right:
        while True:
            available, _, _ = select.select([left, right], [], [], 10)
            if not available:
                return
            for source in available:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)


def dns_answer(packet, address):
    end = 12
    while packet[end]:
        end += packet[end] + 1
    end += 1
    kind = int.from_bytes(packet[end : end + 2], "big")
    end += 4
    value = socket.inet_pton(
        socket.AF_INET6 if kind == 28 else socket.AF_INET,
        TARGET6 if kind == 28 else address,
    )
    return (
        packet[:2]
        + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
        + packet[12:end]
        + b"\xc0\x0c"
        + struct.pack("!HHIH", kind, 1, 30, len(value))
        + value
    )


def dns_server(address, answer):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((address, 53))
    while True:
        packet, peer = sock.recvfrom(4096)
        sock.sendto(dns_answer(packet, answer), peer)


def serve_upstream():
    class Origin(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = b"proxied" if proxied(self.client_address[0]) else b"direct"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Origin6(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    class SOCKS(StreamRequestHandler):
        def handle(self):
            try:
                head = receive(self.rfile, 2)
                receive(self.rfile, head[1])
                self.wfile.write(b"\x05\x00")
                self.wfile.flush()
                request = receive(self.rfile, 3)
                _, port = socks_address(self.rfile)
                if request[1] == 1:
                    connection = socket.create_connection(
                        ("127.0.0.1", port), timeout=5
                    )
                    self.wfile.write(
                        b"\x05\x00\x00\x01" + socket.inet_aton(UPSTREAM) + b"\x00\x00"
                    )
                    self.wfile.flush()
                    relay(self.connection, connection)
                elif request[1] == 3:
                    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    udp.bind((UPSTREAM, 0))
                    self.wfile.write(
                        b"\x05\x00\x00\x01"
                        + socket.inet_aton(UPSTREAM)
                        + struct.pack("!H", udp.getsockname()[1])
                    )
                    self.wfile.flush()

                    def datagrams():
                        import io

                        while True:
                            packet, peer = udp.recvfrom(65535)
                            stream = io.BytesIO(packet[3:])
                            header, destination = socks_address(stream)
                            with socket.socket(
                                socket.AF_INET, socket.SOCK_DGRAM
                            ) as outgoing:
                                outgoing.settimeout(5)
                                outgoing.sendto(
                                    stream.read(), ("127.0.0.1", destination)
                                )
                                data = outgoing.recv(65535)
                            udp.sendto(b"\x00\x00\x00" + header + data, peer)

                    threading.Thread(target=datagrams, daemon=True).start()
                    self.rfile.read(1)
            except (OSError, EOFError):
                return

    def echo(address):
        udp = socket.socket(
            socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_DGRAM
        )
        udp.bind((address, 18333))
        while True:
            data, peer = udp.recvfrom(65535)
            udp.sendto((b"proxied:" if proxied(peer[0]) else b"direct:") + data, peer)

    # Bind each origin address explicitly so UDP replies retain the destination
    # address and match the simulated Docker NAT conntrack tuple.
    for function, args in (
        (dns_server, (UPSTREAM, TARGET)),
        (echo, (TARGET,)),
        (echo, ("127.0.0.1",)),
        (echo, (TARGET6,)),
    ):
        threading.Thread(target=function, args=args, daemon=True).start()
    for server in (
        Origin6(("::", 18080), Origin),
        ThreadingTCPServer((UPSTREAM, 1080), SOCKS),
    ):
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print("ready", flush=True)
    threading.Event().wait()


def probe(kind, destination=TARGET):
    if kind == "tcp":
        url = (
            "http://"
            + (f"[{destination}]" if ":" in destination else destination)
            + ":18080/probe"
        )
        with build_opener(ProxyHandler({})).open(url, timeout=5) as response:
            return response.read().decode()
    if kind == "udp":
        with socket.socket(
            socket.AF_INET6 if ":" in destination else socket.AF_INET, socket.SOCK_DGRAM
        ) as sock:
            sock.settimeout(5)
            sock.sendto(b"fixture", (destination, 18333))
            return sock.recv(1024).decode()
    if kind == "dns":
        packet = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07fixture\x04test\x00\x00\x01\x00\x01"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(5)
            sock.sendto(packet, (destination, 53))
            return socket.inet_ntoa(sock.recv(4096)[-4:])
    if kind == "share":
        request = __import__("urllib.request", fromlist=["Request"]).Request(
            "http://" + TARGET + ":18080/shared"
        )
        import base64

        request.add_header(
            "Proxy-Authorization",
            "Basic " + base64.b64encode(b"fixture:fixture-password").decode(),
        )
        with build_opener(ProxyHandler({"http": "http://172.30.32.1:7890"})).open(
            request, timeout=5
        ) as response:
            return response.read().decode()
    raise ValueError("Invalid probe")


def main():
    if os.environ.get("NP_ISOLATED_TEST") != "1":
        raise SystemExit(
            "Run only in an isolated disposable network-none test container"
        )
    run("ip", "link", "set", "lo", "up")
    for name in ("np-server", "np-a", "np-b"):
        run("ip", "netns", "add", name)
        run("ip", "netns", "exec", name, "ip", "link", "set", "lo", "up")
    run("ip", "link", "add", "wan0", "type", "veth", "peer", "name", "peer0")
    run("ip", "link", "set", "peer0", "netns", "np-server")
    run("ip", "addr", "add", "192.168.200.1/24", "dev", "wan0")
    run("ip", "-6", "addr", "add", "2001:db8:200::1/64", "dev", "wan0", "nodad")
    run("ip", "link", "set", "wan0", "up")
    for address in (UPSTREAM + "/24", TARGET + "/32"):
        run(
            "ip",
            "netns",
            "exec",
            "np-server",
            "ip",
            "addr",
            "add",
            address,
            "dev",
            "peer0",
        )
    for address in ("2001:db8:200::2/64", TARGET6 + "/128"):
        run(
            "ip",
            "netns",
            "exec",
            "np-server",
            "ip",
            "-6",
            "addr",
            "add",
            address,
            "dev",
            "peer0",
            "nodad",
        )
    run("ip", "netns", "exec", "np-server", "ip", "link", "set", "peer0", "up")
    run(
        "ip",
        "netns",
        "exec",
        "np-server",
        "ip",
        "route",
        "add",
        "default",
        "via",
        "192.168.200.1",
    )
    run("ip", "route", "add", "default", "via", UPSTREAM)
    run("ip", "-6", "route", "add", "default", "via", "2001:db8:200::2")
    run("ip", "link", "add", "hassio", "type", "bridge")
    run("ip", "addr", "add", "172.30.32.1/23", "dev", "hassio")
    run("ip", "addr", "add", "172.30.32.3/32", "dev", "lo")
    run("ip", "link", "set", "hassio", "up")
    for name, address in (("a", "172.30.33.10"), ("b", "172.30.33.11")):
        run("ip", "link", "add", "v" + name, "type", "veth", "peer", "name", "p" + name)
        run("ip", "link", "set", "p" + name, "netns", "np-" + name)
        run("ip", "link", "set", "v" + name, "master", "hassio")
        run("ip", "link", "set", "v" + name, "up")
        run(
            "ip",
            "netns",
            "exec",
            "np-" + name,
            "ip",
            "addr",
            "add",
            address + "/23",
            "dev",
            "p" + name,
        )
        run("ip", "netns", "exec", "np-" + name, "ip", "link", "set", "p" + name, "up")
        run(
            "ip",
            "netns",
            "exec",
            "np-" + name,
            "ip",
            "route",
            "add",
            "default",
            "via",
            "172.30.32.1",
        )
    Path("/proc/sys/net/ipv4/ip_forward").write_text("1")
    run("iptables", "-P", "FORWARD", "DROP")
    run("iptables", "-A", "FORWARD", "-i", "hassio", "-o", "wan0", "-j", "ACCEPT")
    run(
        "iptables",
        "-A",
        "FORWARD",
        "-m",
        "conntrack",
        "--ctstate",
        "ESTABLISHED,RELATED",
        "-j",
        "ACCEPT",
    )
    run(
        "iptables",
        "-t",
        "nat",
        "-A",
        "POSTROUTING",
        "-s",
        "172.30.32.0/23",
        "-o",
        "wan0",
        "-j",
        "MASQUERADE",
    )
    threading.Thread(
        target=dns_server, args=("172.30.32.3", "198.51.100.55"), daemon=True
    ).start()
    script = str(Path(__file__).resolve())
    upstream = subprocess.Popen(
        ["ip", "netns", "exec", "np-server", sys.executable, script, "serve"],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert upstream.stdout.readline().strip() == "ready"
    with tempfile.TemporaryDirectory() as directory:
        document = {
            "proxies": [
                {
                    "name": "Fixture",
                    "type": "socks5",
                    "server": UPSTREAM,
                    "port": 1080,
                    "udp": True,
                }
            ],
            "proxy-groups": [
                {"name": "Policy", "type": "select", "proxies": ["Fixture"]}
            ],
            "rules": [
                f"IP-CIDR,{TARGET}/32,Policy,no-resolve",
                f"IP-CIDR6,{TARGET6}/128,Policy,no-resolve",
                "MATCH,DIRECT",
            ],
            "dns": {
                "nameserver": [UPSTREAM + ":53"],
                "default-nameserver": [UPSTREAM],
                "enhanced-mode": "redir-host",
            },
        }
        client = CoreClient("fixture-secret-at-least-16")
        traffic = Traffic(directory)
        # Only inventory is simulated; privileges, routes, TUN and sockets are real.
        traffic.apps = [
            {"id": "a", "ip": "172.30.33.10", "supported": True, "state": "started"}
        ]
        traffic.inventory_ok = True
        traffic.refresh_inventory = lambda: None
        store = SettingsStore(
            directory,
            {"controller_secret": client.secret},
            client.reload,
            fetcher=lambda url: document,
            traffic=traffic,
        )
        store.sources = {
            "subscription_url": "https://fixture.test/sub",
            "outlet_source": "subscription",
        }
        store.prepare()
        config = generate(
            {**store.options, **store.sources}, subscription_document=document
        )
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(config))
        log = (Path(directory) / "core.log").open("w+")
        core = subprocess.Popen(
            ["mihomo", "-d", directory, "-f", str(path)], stdout=log, stderr=log
        )
        try:
            for _ in range(100):
                try:
                    client.request("GET", "/version")
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("Core did not start")

            def check(namespace, kind, expected, address=TARGET):
                result = (
                    probe(kind, address)
                    if namespace is None
                    else run(
                        "ip",
                        "netns",
                        "exec",
                        namespace,
                        sys.executable,
                        script,
                        "probe",
                        kind,
                        address,
                    )
                )
                assert result == expected, (namespace, kind, address, result, expected)

            check(None, "tcp", "direct")
            check("np-a", "udp", "direct:fixture")
            store.update({"traffic_mode": "selected", "selected_apps": ["a"]})
            check("np-a", "tcp", "proxied")
            check("np-a", "udp", "proxied:fixture")
            check("np-a", "dns", TARGET, "172.30.32.3")
            check("np-b", "tcp", "direct")
            check("np-b", "udp", "direct:fixture")
            check("np-b", "dns", "198.51.100.55", "172.30.32.3")
            check(None, "tcp", "direct")
            store.update({"traffic_mode": "haos"})
            for namespace in (None, "np-a", "np-b"):
                check(namespace, "tcp", "proxied")
                check(namespace, "udp", "proxied:fixture")
                check(namespace, "dns", TARGET, "172.30.32.3")
            check(None, "tcp", "proxied", TARGET6)
            check(None, "udp", "proxied:fixture", TARGET6)
            check(None, "tcp", "direct", UPSTREAM)
            store.update({"core_mode": "direct"})
            check(None, "tcp", "direct")
            store.update({"core_mode": "global"})
            check(None, "tcp", "proxied")
            store.update(
                {
                    "traffic_mode": "explicit",
                    "core_mode": "rule",
                    "share_enabled": True,
                    "proxy_username": "fixture",
                    "proxy_password": "fixture-password",
                }
            )
            check("np-b", "share", "proxied")
            check(None, "tcp", "direct")
            check("np-a", "tcp", "direct")
            check("np-a", "dns", "198.51.100.55", "172.30.32.3")
            assert not traffic.path.exists()
            assert "20227" not in run("ip", "rule", "show")
            print(
                "TUN PASS: selected/unselected apps; whole host/apps; TCP, UDP, DNS, IPv6; private bypass; direct/global; LAN sharing; cleanup",
                flush=True,
            )
        except Exception:
            log.flush()
            print(Path(log.name).read_text()[-10000:])
            raise
        finally:
            traffic.cleanup()
            core.terminate()
            core.wait(timeout=10)
            upstream.terminate()
            upstream.wait(timeout=5)
            log.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve_upstream()
    elif len(sys.argv) > 1 and sys.argv[1] == "probe":
        print(probe(sys.argv[2], sys.argv[3]))
    else:
        main()
