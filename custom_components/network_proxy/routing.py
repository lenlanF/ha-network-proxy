"""Experimental aiohttp routing restricted to HA config-entry task context.

This uses a private aiohttp hook; it intentionally has no heuristic stack
attribution and cannot route requests that have lost their config-entry context.
"""

import inspect
import ipaddress
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from aiohttp import BasicAuth, ClientSession


def local_url(url):
    try:
        host = (urlsplit(str(url)).hostname or "").lower().rstrip(".")
        if (
            not host
            or host == "localhost"
            or host.endswith((".local", ".localhost", ".lan"))
            or "." not in host
        ):
            return True
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


@dataclass
class Policy:
    targets: set[str]
    url: str
    auth: BasicAuth | None = None
    bypass_local: bool = True
    attempts: Counter = field(default_factory=Counter)
    responses: Counter = field(default_factory=Counter)
    failures: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)


class Router:
    def __init__(self, context_getter, session_class=ClientSession):
        self.context_getter = context_getter
        self.session_class = session_class
        self.policies = {}
        self.original = None
        self.wrapper = None

    def add(self, owner, policy):
        for other_owner, other in self.policies.items():
            if other_owner != owner and policy.targets & other.targets:
                raise ValueError("Target already assigned to another proxy")
        if self.original is None:
            original = self.session_class._request
            if not {"proxy", "proxy_auth"}.issubset(
                inspect.signature(original).parameters
            ):
                raise RuntimeError("Incompatible aiohttp request signature")
            self.original = original

            async def routed(session, method, url, **kwargs):
                entry = self.context_getter()
                target = getattr(entry, "entry_id", None)
                selected = next(
                    (p for p in self.policies.values() if target in p.targets), None
                )
                if selected is None:
                    return await original(session, method, url, **kwargs)
                if "proxy" in kwargs or (selected.bypass_local and local_url(url)):
                    selected.skipped[target] += 1
                    return await original(session, method, url, **kwargs)
                kwargs["proxy"] = selected.url
                if selected.auth:
                    kwargs["proxy_auth"] = selected.auth
                selected.attempts[target] += 1
                try:
                    response = await original(session, method, url, **kwargs)
                except Exception:
                    selected.failures[target] += 1
                    raise
                selected.responses[target] += 1
                return response

            self.wrapper = routed
            self.session_class._request = routed
        elif self.session_class._request is not self.wrapper:
            raise RuntimeError("Another component replaced the request hook")
        self.policies[owner] = policy

    def remove(self, owner):
        self.policies.pop(owner, None)
        if not self.policies and self.original:
            if self.session_class._request is self.wrapper:
                self.session_class._request = self.original
            self.original = self.wrapper = None
