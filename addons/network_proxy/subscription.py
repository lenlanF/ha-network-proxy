"""Bounded YAML subscriptions, deterministic overrides and private snapshots."""

import copy
import hashlib
import json
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import yaml

MAX_BYTES = 2 * 1024 * 1024


class ConfigError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class UniqueLoader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    explicit = set()
    for key, _value in node.value:
        if key.tag == "tag:yaml.org,2002:merge":
            continue
        name = loader.construct_object(key)
        if not isinstance(name, str) or name in explicit:
            raise ConfigError("invalid_yaml")
        explicit.add(name)
    loader.flatten_mapping(node)
    result = {}
    for key, value in loader.construct_pairs(node, deep=deep):
        if not isinstance(key, str):
            raise ConfigError("invalid_yaml")
        result[key] = value
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def unique_json_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("invalid_yaml")
        result[key] = value
    return result


def parse_document(body, *, empty=False):
    try:
        size = len(body.encode() if isinstance(body, str) else body)
    except UnicodeError as error:
        raise ConfigError("invalid_yaml") from error
    if size > MAX_BYTES:
        raise ConfigError("config_too_large")
    try:
        loader = UniqueLoader(body)
        try:
            node = loader.get_single_node()
            budget = [50000]

            def inspect_node(current, ancestors=(), depth=0):
                budget[0] -= 1
                if budget[0] < 0 or depth > 32 or id(current) in ancestors:
                    raise ConfigError("invalid_yaml")
                descendants = (*ancestors, id(current))
                if isinstance(current, yaml.MappingNode):
                    for key, value in current.value:
                        inspect_node(key, descendants, depth + 1)
                        inspect_node(value, descendants, depth + 1)
                elif isinstance(current, yaml.SequenceNode):
                    for value in current.value:
                        inspect_node(value, descendants, depth + 1)

            if node is not None:
                # Count expanded aliases before merge constructors can allocate
                # an exponential mapping or recurse through an alias cycle.
                inspect_node(node)
            doc = loader.construct_document(node) if node is not None else None
        finally:
            loader.dispose()
    except (yaml.YAMLError, UnicodeError, RecursionError) as error:
        raise ConfigError("invalid_yaml") from error
    # JSON surrogate pairs represent one Unicode character. The YAML loader
    # leaves them as separate surrogate code points, which cannot be UTF-8.
    try:
        doc = json.loads(body, object_pairs_hook=unique_json_mapping)
    except json.JSONDecodeError:
        pass
    except (UnicodeError, RecursionError) as error:
        raise ConfigError("invalid_yaml") from error
    if doc is None and empty:
        return {}
    if not isinstance(doc, dict):
        raise ConfigError("invalid_yaml")
    budget = [50000]

    def walk(value, ancestors=(), depth=0):
        budget[0] -= 1
        if depth > 32 or budget[0] < 0 or id(value) in ancestors:
            raise ConfigError("invalid_yaml")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ConfigError("invalid_yaml")
                try:
                    key.encode("utf-8")
                except UnicodeError as error:
                    raise ConfigError("invalid_yaml") from error
                walk(child, (*ancestors, id(value)), depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, (*ancestors, id(value)), depth + 1)
        elif isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeError as error:
                raise ConfigError("invalid_yaml") from error
        elif value is not None and type(value) not in (int, float, bool):
            raise ConfigError("invalid_yaml")

    walk(doc)
    try:
        json.dumps(doc, allow_nan=False)
    except ValueError as error:
        raise ConfigError("invalid_yaml") from error
    return doc


def validate_url(url):
    if not isinstance(url, str):
        raise ConfigError("subscription_invalid")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.fragment:
        raise ConfigError("subscription_invalid")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ConfigError("subscription_invalid") from error


def fetch_document(url):
    validate_url(url)
    request = Request(url, headers={"User-Agent": "Clash.Meta/NetworkProxy-0.4.3"})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
            validate_url(response.url)
            body = response.read(MAX_BYTES + 1)
    except (OSError, ValueError) as error:
        raise ConfigError("subscription_fetch_failed") from error
    return parse_document(body)


def url_digest(url):
    return hashlib.sha256(url.encode()).hexdigest()


def deep_merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


MANAGED = {
    "port",
    "socks-port",
    "mixed-port",
    "redir-port",
    "tproxy-port",
    "tun",
    "listeners",
    "tunnels",
    "external-controller",
    "external-controller-tls",
    "external-controller-unix",
    "external-controller-pipe",
    "external-ui",
    "external-ui-url",
    "external-ui-name",
    "external-doh-server",
    "external-controller-cors",
    "secret",
    "authentication",
    "skip-auth-prefixes",
    "allow-lan",
    "bind-address",
    "lan-allowed-ips",
    "lan-disallowed-ips",
    "routing-mark",
    "interface-name",
    "iptables",
    "ntp",
    "ss-config",
    "vmess-config",
    "tuic-server",
    "tls",
    "script",
    "mode",
}


def compose(document, extension):
    overlay = parse_document(extension, empty=True)
    if MANAGED & overlay.keys() or (
        isinstance(overlay.get("dns"), dict) and "listen" in overlay["dns"]
    ):
        raise ConfigError("managed_setting")
    base = {
        key: copy.deepcopy(value)
        for key, value in document.items()
        if key not in MANAGED
    }
    if isinstance(base.get("dns"), dict):
        base["dns"].pop("listen", None)
    before, after = overlay.pop("prepend-rules", []), overlay.pop("append-rules", [])
    if not isinstance(before, list) or not isinstance(after, list):
        raise ConfigError("invalid_yaml")
    result = deep_merge(base, overlay)
    for proxy in (
        result.get("proxies", []) if isinstance(result.get("proxies"), list) else []
    ):
        if isinstance(proxy, dict):
            proxy.pop("routing-mark", None)
            proxy.pop("interface-name", None)
    if any(
        key in result and not isinstance(result[key], dict)
        for key in ("dns", "profile", "sniffer")
    ):
        raise ConfigError("subscription_invalid")
    rules = result.get("rules", [])
    if not isinstance(rules, list) or any(
        not isinstance(item, str) for item in rules + before + after
    ):
        raise ConfigError("invalid_yaml")
    final = next(
        (
            i
            for i, item in enumerate(rules)
            if item.split(",", 1)[0] in ("MATCH", "FINAL")
        ),
        len(rules),
    )
    result["rules"] = before + rules[:final] + after + rules[final:]
    for kind in ("proxy-providers", "rule-providers"):
        providers = result.get(kind, {})
        if not isinstance(providers, dict):
            raise ConfigError("subscription_invalid")
        for name, provider in providers.items():
            if not isinstance(provider, dict) or provider.get("type") not in (
                "http",
                "inline",
            ):
                raise ConfigError("provider_unsupported")
            if provider.get("type") == "http":
                validate_url(provider.get("url", ""))
                provider["path"] = (
                    "./providers/" + kind + "-" + url_digest(name)[:16] + ".yaml"
                )
    return result
