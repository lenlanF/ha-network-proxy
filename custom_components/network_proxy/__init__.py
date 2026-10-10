"""Mihomo control and explicitly enabled per-entry aiohttp interception."""

import logging
from datetime import timedelta

from aiohttp import BasicAuth, ClientError
from homeassistant.config_entries import current_entry
from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Manager
from .const import DOMAIN
from .routing import Policy, Router

_LOGGER = logging.getLogger(__name__)
_ROUTER = Router(current_entry.get)
PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass, entry):
    config = {**entry.data, **entry.options}
    api = Manager(
        async_get_clientsession(hass), config["manager_url"], config["secret"]
    )
    policy = Policy(set(), config["proxy_url"])

    async def update():
        try:
            await api.report(
                [
                    {
                        "id": item.entry_id,
                        "name": item.title,
                        "domain": item.domain,
                        "disabled": bool(item.disabled_by),
                    }
                    for item in hass.config_entries.async_entries()
                    if item.domain != DOMAIN
                ]
            )
            data = await api.status()
            settings = await api.runtime()
            targets = (
                set(settings["targets"])
                if settings["traffic_mode"] == "selected"
                else set()
            )
            available = {
                item.entry_id
                for item in hass.config_entries.async_entries()
                if item.domain != DOMAIN and not item.disabled_by
            }
            policy.targets = targets & available
            policy.url = settings["proxy_url"]
            policy.auth = (
                BasicAuth(settings["proxy_username"], settings["proxy_password"])
                if settings.get("proxy_username")
                else None
            )
            if policy.targets:
                _ROUTER.add(entry.entry_id, policy)
            else:
                _ROUTER.remove(entry.entry_id)
            return data
        except (ClientError, TimeoutError, KeyError, ValueError, RuntimeError) as error:
            raise UpdateFailed(
                "Proxy application unavailable or routing incompatible"
            ) from error

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=update,
        update_interval=timedelta(seconds=10),
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        _ROUTER.remove(entry.entry_id)
        raise
    runtime = {
        "api": api,
        "coordinator": coordinator,
        "entry": entry,
        "config": config,
        "policy": policy,
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        _ROUTER.remove(entry.entry_id)
        hass.data[DOMAIN].pop(entry.entry_id)
        raise
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        _ROUTER.remove(entry.entry_id)
        hass.data[DOMAIN].pop(entry.entry_id)
        return True
    return False
