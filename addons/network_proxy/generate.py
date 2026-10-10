"""Generate a restricted Mihomo config; never execute subscription configuration."""

import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlparse


def generate(options, *, bootstrap=False):
    secret = options.get("controller_secret", "")
    if len(secret) < 16:
        raise ValueError("controller_secret must contain at least 16 characters")
    config = {
        "mixed-port": 7890,
        "allow-lan": True,
        "bind-address": "*",
        "external-controller": "0.0.0.0:9090",
        "secret": secret,
        "mode": "rule",
        "log-level": "warning",
        "profile": {"store-selected": True},
        "proxies": [],
    }
    names = []
    user, password = options.get("proxy_username"), options.get("proxy_password")
    if bool(user) != bool(password):
        raise ValueError("Set both proxy username and password")
    if user:
        if ":" in user:
            raise ValueError("Proxy username cannot contain colon")
        config["authentication"] = [f"{user}:{password}"]
    subscription = options.get("subscription_url")
    if subscription:
        parsed = urlparse(subscription)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Subscription must be an HTTP(S) URL")
        config["proxy-providers"] = {
            "subscription": {
                "type": "http",
                "url": subscription,
                "path": "./providers/subscription.yaml",
                "interval": 3600,
                "health-check": {
                    "enable": True,
                    "url": "https://www.gstatic.com/generate_204",
                    "interval": 300,
                },
            }
        }
    if options.get("lan_host"):
        kind = options.get("lan_type", "http")
        port = int(options.get("lan_port", 7890))
        if kind not in ("http", "socks5") or not 1 <= port <= 65535:
            raise ValueError("Invalid LAN proxy type or port")
        proxy = {
            "name": "LAN",
            "type": kind,
            "server": options["lan_host"],
            "port": port,
        }
        if options.get("lan_username"):
            proxy.update(
                username=options["lan_username"],
                password=options.get("lan_password", ""),
            )
        config["proxies"].append(proxy)
        names.append("LAN")
    if not subscription and not names and not bootstrap:
        raise ValueError("Configure a subscription or LAN proxy")
    # Do not silently fall back to DIRECT when a source is configured.
    group = {"name": "PROXY", "type": "select", "proxies": names}
    if subscription:
        group["use"] = ["subscription"]
    if not subscription and not names:
        group["proxies"] = ["REJECT"]
    config["proxy-groups"] = [group]
    config["rules"] = [
        "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
        "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
        "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
        "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
        "IP-CIDR6,::1/128,DIRECT,no-resolve",
        "IP-CIDR6,fc00::/7,DIRECT,no-resolve",
        "IP-CIDR6,fe80::/10,DIRECT,no-resolve",
    ]
    for action in ("direct", "proxy"):
        target = "DIRECT" if action == "direct" else "PROXY"
        domains = options.get(f"{action}_domains", [])
        cidrs = options.get(f"{action}_cidrs", [])
        if (
            not isinstance(domains, list)
            or not isinstance(cidrs, list)
            or len(domains) + len(cidrs) > 128
        ):
            raise ValueError("Too many rules")
        for domain in domains:
            if (
                not isinstance(domain, str)
                or len(domain) > 253
                or not re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain
                )
            ):
                raise ValueError("Invalid domain rule")
            if any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                for label in domain.split(".")
            ):
                raise ValueError("Invalid domain label")
            config["rules"].append(f"DOMAIN-SUFFIX,{domain.lower()},{target}")
        for cidr in cidrs:
            if not isinstance(cidr, str):
                raise TypeError("Invalid CIDR rule")
            network = ipaddress.ip_network(cidr, strict=False)
            kind = "IP-CIDR" if network.version == 4 else "IP-CIDR6"
            config["rules"].append(f"{kind},{network},{target},no-resolve")
    default_action = options.get("default_action", "proxy")
    if default_action not in ("proxy", "direct"):
        raise ValueError("Invalid default action")
    config["rules"].append(
        "MATCH," + ("PROXY" if default_action == "proxy" else "DIRECT")
    )
    return config


if __name__ == "__main__":
    options = json.loads(Path("/data/options.json").read_text())
    Path("/data/config.json").write_text(json.dumps(generate(options)))
