"""Independent subscription profiles; native policies are never merged together."""

import copy
import re

from subscription import ConfigError, validate_url

PROFILE_KEYS = {"id", "name", "url", "interval", "extension_yaml", "config_mode"}


def validate_profiles(profiles):
    if not isinstance(profiles, list) or len(profiles) > 32:
        raise ConfigError("invalid_profiles")
    seen = set()
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) - PROFILE_KEYS:
            raise ConfigError("invalid_profiles")
        identity = profile.get("id", "")
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity)
            or identity in seen
        ):
            raise ConfigError("invalid_profiles")
        seen.add(identity)
        if (
            not isinstance(profile.get("name"), str)
            or not 1 <= len(profile["name"].strip()) <= 128
        ):
            raise ConfigError("invalid_profiles")
        validate_url(profile.get("url"))
        if len(profile["url"]) > 8192:
            raise ConfigError("invalid_profiles")
        interval = profile.get("interval", 3600)
        if type(interval) is not int or interval != 0 and not 300 <= interval <= 604800:
            raise ConfigError("invalid_profiles")
        extension = profile.get("extension_yaml", "")
        if not isinstance(extension, str) or len(extension.encode()) > 131072:
            raise ConfigError("invalid_profiles")
        if profile.get("config_mode", "native") not in ("native", "edited"):
            raise ConfigError("invalid_profiles")


def normalize(sources, changes=None):
    """Migrate single subscriptions and maintain the legacy active-source fields."""
    result = copy.deepcopy(sources)
    changes = changes or {}
    profiles = result.get("subscriptions", [])
    if (
        not profiles
        and result.get("subscription_url")
        and "subscriptions" not in changes
    ):
        profiles = [
            {
                "id": "legacy",
                "name": "默认订阅",
                "url": result["subscription_url"],
                "interval": result.get("subscription_interval", 3600),
                "extension_yaml": result.get("extension_yaml", ""),
                "config_mode": "native",
            }
        ]
        result["subscriptions"] = profiles
        result["active_subscription"] = "legacy"
    elif profiles and "subscriptions" not in changes:
        active = result.get("active_subscription") or profiles[0]["id"]
        profile = next((item for item in profiles if item["id"] == active), None)
        if profile:
            for legacy, key in (
                ("subscription_url", "url"),
                ("subscription_interval", "interval"),
                ("extension_yaml", "extension_yaml"),
            ):
                if legacy in changes:
                    profile[key] = changes[legacy]
            if not profile.get("url"):
                profiles.remove(profile)
    validate_profiles(profiles)
    identities = {item["id"] for item in profiles}
    if (
        "active_subscription" in changes
        and changes["active_subscription"] not in identities
        and profiles
    ):
        raise ConfigError("profile_not_found")
    if profiles:
        active = result.get("active_subscription")
        if active not in identities:
            active = profiles[0]["id"]
        profile = next(item for item in profiles if item["id"] == active)
        result.update(
            subscriptions=profiles,
            active_subscription=active,
            subscription_url=profile["url"],
            subscription_interval=profile.get("interval", 3600),
            extension_yaml=profile.get("extension_yaml", ""),
        )
    elif "subscriptions" in result:
        result.update(
            subscriptions=[],
            active_subscription="",
            subscription_url="",
            extension_yaml="",
        )
    return result


def activate(sources, identity):
    return normalize(
        {**sources, "active_subscription": identity},
        {
            "active_subscription": identity,
            "subscriptions": sources.get("subscriptions", []),
        },
    )


def active_profile(sources):
    return next(
        (
            item
            for item in sources.get("subscriptions", [])
            if item["id"] == sources.get("active_subscription")
        ),
        {},
    )


def selected_document(profile, snapshot):
    if profile.get("config_mode") == "edited":
        document = snapshot.get("edited_document")
        if document is None:
            raise ConfigError("profile_not_ready")
        return document
    return snapshot.get("document")
