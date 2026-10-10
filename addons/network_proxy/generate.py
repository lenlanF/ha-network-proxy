"""Compile subscription policies or a shared upstream into one managed outlet."""

import ipaddress
import json
import re
from pathlib import Path

from subscription import ConfigError, compose

ROOT_GROUP = "NETWORK_PROXY_OUTLET"
LOCAL_RANGES = [
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "169.254.0.0/16",
    "224.0.0.0/4",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
]
LOCAL_RULES = [
    f"{'IP-CIDR6' if ':' in cidr else 'IP-CIDR'},{cidr},DIRECT,no-resolve"
    for cidr in LOCAL_RANGES
]


def source_choice(options):
    return options.get("outlet_source") or (
        "subscription" if options.get("subscription_url") else "lan"
    )


def extra_rules(options):
    rules = []
    for action in ("direct", "proxy"):
        target = "DIRECT" if action == "direct" else ROOT_GROUP
        domains, cidrs = (
            options.get(f"{action}_domains", []),
            options.get(f"{action}_cidrs", []),
        )
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
            rules.append(f"DOMAIN-SUFFIX,{domain.lower()},{target}")
        for cidr in cidrs:
            if not isinstance(cidr, str):
                raise TypeError("Invalid CIDR rule")
            network = ipaddress.ip_network(cidr, strict=False)
            rules.append(
                f"{'IP-CIDR6' if network.version == 6 else 'IP-CIDR'},{network},{target},no-resolve"
            )
    return rules


def generate(options, *, bootstrap=False, subscription_document=None):
    secret = options.get("controller_secret", "")
    if len(secret) < 16:
        raise ValueError("controller_secret must contain at least 16 characters")
    choice = source_choice(options)
    if choice not in ("subscription", "lan"):
        raise ValueError("Invalid outlet source")
    enabled = options.get("proxy_enabled", True)
    mode, scope = (
        options.get("core_mode", "native"),
        options.get("traffic_mode", "explicit"),
    )
    if mode not in ("native", "rule", "global", "direct") or scope not in (
        "explicit",
        "haos",
        "selected",
    ):
        raise ValueError("Invalid mode")
    config, target = {}, "REJECT"
    if choice == "subscription" and options.get("subscription_url"):
        if subscription_document is None:
            if not bootstrap:
                raise ConfigError("subscription_not_ready")
        else:
            config = compose(
                subscription_document,
                options.get("extension_yaml", ""),
                namespace=options.get("active_subscription", ""),
            )
            proxies, groups = config.get("proxies", []), config.get("proxy-groups", [])
            if not isinstance(proxies, list) or not isinstance(groups, list):
                raise ConfigError("subscription_invalid")
            if any(
                not isinstance(item, dict) or not isinstance(item.get("name"), str)
                for item in proxies + groups
            ):
                raise ConfigError("subscription_invalid")
            if not proxies and not config.get("proxy-providers"):
                raise ConfigError("subscription_empty")
            if not groups:
                groups = [
                    {
                        "name": "订阅代理",
                        "type": "select",
                        "proxies": [item["name"] for item in proxies],
                    }
                ]
                if config.get("proxy-providers"):
                    groups[0]["use"] = list(config["proxy-providers"])
                config["proxy-groups"] = groups
            target = groups[0]["name"]
            config.setdefault("proxies", [])
    elif choice == "lan" and options.get("lan_host"):
        kind, port = options.get("lan_type", "http"), int(options.get("lan_port", 7890))
        if kind not in ("http", "socks5") or not 1 <= port <= 65535:
            raise ValueError("Invalid LAN proxy")
        proxy = {
            "name": "LAN",
            "type": kind,
            "server": options["lan_host"],
            "port": port,
        }
        if kind == "socks5":
            proxy["udp"] = True
        if options.get("lan_username"):
            proxy.update(
                username=options["lan_username"],
                password=options.get("lan_password", ""),
            )
        config, target = {"proxies": [proxy], "proxy-groups": []}, "LAN"
    elif not bootstrap:
        raise ValueError("Selected source is not configured")
    if mode == "native":
        mode = (
            (subscription_document or {}).get("mode", "rule")
            if choice == "subscription"
            else "rule"
        )
        if mode not in ("rule", "global", "direct"):
            raise ConfigError("subscription_invalid")
    if not enabled:
        scope = "explicit"
    if target == "REJECT" and scope != "explicit":
        raise ConfigError("source_required")
    config.setdefault("proxies", [])
    config.setdefault("proxy-groups", [])
    if any(
        item.get("name") == ROOT_GROUP
        for item in config["proxies"] + config["proxy-groups"]
    ):
        raise ConfigError("reserved_group")
    config["proxy-groups"].append(
        {"name": ROOT_GROUP, "type": "select", "proxies": [target]}
    )
    supplied_rules = config.get("rules", [])
    if not supplied_rules:
        default = options.get("default_action", "proxy")
        if default not in ("proxy", "direct"):
            raise ValueError("Invalid default action")
        supplied_rules = ["MATCH," + (ROOT_GROUP if default == "proxy" else "DIRECT")]
    config["rules"] = LOCAL_RULES + extra_rules(options) + supplied_rules
    share = enabled and options.get("share_enabled", False)
    user, password = (
        options.get("proxy_username", ""),
        options.get("proxy_password", ""),
    )
    if bool(user) != bool(password) or ":" in user:
        raise ValueError("Set a valid outlet credential pair")
    config.update(
        {
            "mixed-port": 7890 if enabled else 0,
            "allow-lan": share,
            "bind-address": "*" if share else "127.0.0.1",
            "lan-allowed-ips": [
                "127.0.0.0/8",
                "::1/128",
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "fc00::/7",
            ],
            "external-controller": "127.0.0.1:9090",
            "secret": secret,
            "mode": mode,
            "routing-mark": 20048,
            "log-level": "warning",
            "authentication": [f"{user}:{password}"] if user else [],
            "profile": {**config.get("profile", {}), "store-selected": True},
            "tun": {"enable": False},
        }
    )
    if scope == "haos" or (scope == "selected" and options.get("selected_apps")):
        if scope == "haos":
            config["ipv6"] = True
        dns = config.get("dns", {})
        config["dns"] = {
            "nameserver": ["system"],
            **dns,
            "enable": True,
            "enhanced-mode": "redir-host",
            "listen": ":1053",
        }
        policy = dict(config["dns"].get("nameserver-policy") or {})
        for name in (
            "+.local",
            "+.lan",
            "+.home.arpa",
            "+.local.hass.io",
            "localhost",
            "supervisor",
            "homeassistant",
            "hassio",
        ):
            policy.setdefault(name, ["system"])
        config["dns"]["nameserver-policy"] = policy
        config["tun"] = {
            "enable": True,
            "device": "NetworkProxy",
            "stack": "mixed",
            "auto-route": scope == "haos",
            "auto-redirect": False,
            "auto-redirect-output-mark": 20048,
            "auto-redirect-input-mark": 20049,
            "auto-detect-interface": True,
            "dns-hijack": ["any:53", "tcp://any:53"],
            "route-exclude-address": LOCAL_RANGES,
            "exclude-src-port": [
                7890,
                options.get("_ingress_port", 8099),
                9090,
                9091,
                8123,
                1053,
            ],
            "iproute2-table-index": 20226,
            "iproute2-rule-index": 20226,
        }
        config.setdefault(
            "sniffer",
            {
                "enable": True,
                "parse-pure-ip": True,
                "sniff": {
                    "HTTP": {"ports": [80, "8080-8880"]},
                    "TLS": {"ports": [443, 8443]},
                    "QUIC": {"ports": [443, 8443]},
                },
            },
        )
    return config


if __name__ == "__main__":
    options = json.loads(Path("/data/options.json").read_text(encoding="utf-8"))
    Path("/data/config.json").write_text(
        json.dumps(generate(options, bootstrap=True), ensure_ascii=False),
        encoding="utf-8",
    )
