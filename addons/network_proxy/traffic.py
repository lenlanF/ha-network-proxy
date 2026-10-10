"""Own only named routing resources; read Supervisor inventory with default role."""

import ipaddress
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from generate import LOCAL_RANGES
from subscription import ConfigError

TABLE = "20227"
PREFERENCE = "20227"
DEVICE = "NetworkProxy"
BRIDGE = ipaddress.ip_network("172.30.32.0/23")


class Supervisor:
    def __init__(self, token=None, url="http://supervisor"):
        self.token = (
            token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        )
        self.url = url
        self.opener = build_opener(ProxyHandler({}))

    def get(self, path):
        if not self.token:
            raise OSError("Supervisor unavailable")
        request = Request(
            self.url + path, headers={"Authorization": "Bearer " + self.token}
        )
        with self.opener.open(request, timeout=5) as response:
            result = json.load(response)
        if result.get("result") != "ok":
            raise OSError("Supervisor unavailable")
        return result["data"]

    def inventory(self):
        result = []
        for app in self.get("/supervisor/info").get("addons", []):
            slug = app["slug"]
            info = self.get("/addons/" + slug + "/info")
            address = info.get("ip_address")
            try:
                valid_ip = ipaddress.ip_address(address) in BRIDGE
            except (ValueError, TypeError):
                valid_ip = False
            supported = not info.get("host_network", False) and valid_ip
            result.append(
                {
                    "id": slug,
                    "name": info.get("name", slug),
                    "state": info.get("state", "unknown"),
                    "ip": address if valid_ip else None,
                    "supported": supported,
                    "reason": ""
                    if supported
                    else "host_network"
                    if info.get("host_network")
                    else "no_bridge_address",
                }
            )
        return result

    def lan_addresses(self):
        result = []
        for interface in self.get("/network/info").get("interfaces", []):
            for item in interface.get("ipv4", {}).get("address", []):
                address = ipaddress.ip_interface(item).ip
                if (
                    address.is_private
                    and not address.is_loopback
                    and address not in BRIDGE
                ):
                    result.append(str(address))
        return result


class Traffic:
    def __init__(self, directory, supervisor=None, runner=None):
        self.path = Path(directory) / "traffic-state.json"
        self.supervisor = supervisor or Supervisor()
        self.runner = runner or subprocess.run
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.apps = []
        self.addresses = []
        self.inventory_ok = False

    def refresh_inventory(self):
        try:
            apps = self.supervisor.inventory()
            self.apps = apps
            self.inventory_ok = True
        except (OSError, ValueError, KeyError, TypeError):
            self.inventory_ok = False
        try:
            self.addresses = self.supervisor.lan_addresses()
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def capability(self):
        try:
            cap = next(
                line.split()[1]
                for line in Path("/proc/self/status").read_text().splitlines()
                if line.startswith("CapEff:")
            )
            ready = bool(int(cap, 16) & (1 << 12)) and Path("/dev/net/tun").exists()
        except (OSError, StopIteration, ValueError):
            ready = False
        return ready and all(
            shutil.which(command) for command in ("ip", "iptables", "ip6tables")
        )

    def plan(self, settings):
        scope = settings.get("traffic_mode", "explicit")
        if (
            settings.get("lan_host") in self.addresses
            and settings.get("lan_port", 7890) == 7890
        ):
            raise ConfigError("upstream_loop")
        selected = settings.get("selected_apps", []) if scope == "selected" else []
        if (scope == "haos" or selected) and not self.capability():
            raise ConfigError("tun_unavailable")
        if selected:
            self.refresh_inventory()
            if not self.inventory_ok:
                raise ConfigError("supervisor_unavailable")
            available = {app["id"]: app for app in self.apps}
            if any(
                item not in available or not available[item]["supported"]
                for item in selected
            ):
                raise ConfigError("app_not_isolatable")
        ips = [
            app["ip"]
            for app in self.apps
            if app["id"] in selected and app["state"] == "started"
        ]
        return {
            "scope": scope,
            "ips": sorted(ips),
            "tun": scope == "haos" or bool(selected),
        }

    def status(self):
        return {
            **self.state,
            "active": bool(
                self.state.get("tun") and Path("/sys/class/net/" + DEVICE).exists()
            ),
        }

    def command(self, *args, required=True):
        result = self.runner(
            list(args),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if required and result.returncode:
            raise ConfigError("traffic_apply_failed")
        return result.returncode == 0

    def cleanup(self):
        # Delete only our exact jump specifications and named chains/table.
        if not self.state:
            return
        for binary in ("iptables", "ip6tables"):
            for table, parent, chain in (
                ("nat", "OUTPUT", "NP_DNS_OUT"),
                ("nat", "PREROUTING", "NP_DNS_IN"),
                ("filter", "INPUT", "NP_DNS_GUARD"),
                ("filter", "FORWARD", "NP_FORWARD"),
            ):
                self.command(
                    binary, "-w", "-t", table, "-D", parent, "-j", chain, required=False
                )
                self.command(binary, "-w", "-t", table, "-F", chain, required=False)
                self.command(binary, "-w", "-t", table, "-X", chain, required=False)
        for address in self.state.get("ips", []):
            if ipaddress.ip_address(address) not in BRIDGE:
                raise ConfigError("invalid_traffic_state")
            self.command(
                "ip",
                "rule",
                "del",
                "pref",
                PREFERENCE,
                "from",
                address + "/32",
                "lookup",
                TABLE,
                required=False,
            )
        self.command("ip", "route", "flush", "table", TABLE, required=False)
        self.state = {}
        self.path.unlink(missing_ok=True)

    def chain(self, binary, table, parent, name):
        self.command(binary, "-w", "-t", table, "-N", name)
        self.command(binary, "-w", "-t", table, "-I", parent, "1", "-j", name)

    def apply(self, plan):
        self.cleanup()
        if not plan["tun"]:
            return
        for _ in range(50):
            if self.command("ip", "link", "show", "dev", DEVICE, required=False):
                break
            time.sleep(0.1)
        else:
            raise ConfigError("tun_not_started")
        # Journal before any mutation so interrupted setup can be cleaned next boot.
        self.state = plan
        self.path.write_text(json.dumps(plan))
        try:
            if plan["scope"] == "selected":
                self.command(
                    "ip", "route", "add", "default", "dev", DEVICE, "table", TABLE
                )
                for prefix in LOCAL_RANGES:
                    if ":" not in prefix:
                        self.command(
                            "ip", "route", "add", "throw", prefix, "table", TABLE
                        )
                self.chain("iptables", "filter", "FORWARD", "NP_FORWARD")
                for address in plan["ips"]:
                    self.command(
                        "ip",
                        "rule",
                        "add",
                        "pref",
                        PREFERENCE,
                        "from",
                        address + "/32",
                        "lookup",
                        TABLE,
                    )
                    self.command(
                        "iptables",
                        "-w",
                        "-A",
                        "NP_FORWARD",
                        "-s",
                        address,
                        "-o",
                        DEVICE,
                        "-j",
                        "ACCEPT",
                    )
                    self.command(
                        "iptables",
                        "-w",
                        "-A",
                        "NP_FORWARD",
                        "-d",
                        address,
                        "-i",
                        DEVICE,
                        "-j",
                        "ACCEPT",
                    )
            for binary in (
                ("iptables", "ip6tables") if plan["scope"] == "haos" else ("iptables",)
            ):
                self.chain(binary, "filter", "INPUT", "NP_DNS_GUARD")
                for protocol in ("udp", "tcp"):
                    self.command(
                        binary,
                        "-w",
                        "-A",
                        "NP_DNS_GUARD",
                        "-i",
                        "lo",
                        "-p",
                        protocol,
                        "--dport",
                        "1053",
                        "-j",
                        "ACCEPT",
                    )
                    if binary == "iptables":
                        self.command(
                            binary,
                            "-w",
                            "-A",
                            "NP_DNS_GUARD",
                            "-s",
                            str(BRIDGE),
                            "-p",
                            protocol,
                            "--dport",
                            "1053",
                            "-j",
                            "ACCEPT",
                        )
                    self.command(
                        binary,
                        "-w",
                        "-A",
                        "NP_DNS_GUARD",
                        "-p",
                        protocol,
                        "--dport",
                        "1053",
                        "-j",
                        "REJECT",
                    )
                self.chain(binary, "nat", "PREROUTING", "NP_DNS_IN")
                sources = (
                    [str(BRIDGE)]
                    if plan["scope"] == "haos" and binary == "iptables"
                    else plan["ips"]
                    if binary == "iptables"
                    else []
                )
                for address in sources:
                    for protocol in ("udp", "tcp"):
                        self.command(
                            binary,
                            "-w",
                            "-t",
                            "nat",
                            "-A",
                            "NP_DNS_IN",
                            "-s",
                            address,
                            "-p",
                            protocol,
                            "--dport",
                            "53",
                            "-j",
                            "REDIRECT",
                            "--to-ports",
                            "1053",
                        )
                if plan["scope"] == "haos":
                    self.chain(binary, "nat", "OUTPUT", "NP_DNS_OUT")
                    for protocol in ("udp", "tcp"):
                        self.command(
                            binary,
                            "-w",
                            "-t",
                            "nat",
                            "-A",
                            "NP_DNS_OUT",
                            "-m",
                            "mark",
                            "!",
                            "--mark",
                            "20048",
                            "-p",
                            protocol,
                            "--dport",
                            "53",
                            "-j",
                            "REDIRECT",
                            "--to-ports",
                            "1053",
                        )
        except Exception:
            self.cleanup()
            raise
