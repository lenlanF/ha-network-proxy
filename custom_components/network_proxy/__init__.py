"""Mihomo control and explicitly enabled per-entry aiohttp interception."""

import logging
from datetime import timedelta

from aiohttp import BasicAuth, ClientError
from homeassistant.config_entries import current_entry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Controller
from .const import DOMAIN
from .routing import Policy, Router

_LOGGER = logging.getLogger(__name__)
_ROUTER = Router(current_entry.get)
PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass, entry):
    config = {**entry.data, **entry.options}
    api = Controller(
        async_get_clientsession(hass), config["controller_url"], config["secret"]
    )

    async def update():
        try:
            return await api.status()
        except (ClientError, TimeoutError, KeyError, ValueError) as error:
            raise UpdateFailed(
                "Proxy controller unavailable or incompatible"
            ) from error

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=update,
        update_interval=timedelta(seconds=30),
    )
    await coordinator.async_config_entry_first_refresh()
    policy = Policy(
        set(config.get("targets", [])),
        config["proxy_url"],
        BasicAuth(config["proxy_username"], config["proxy_password"])
        if config.get("proxy_username")
        else None,
        config.get("bypass_local", True),
    )
    if config.get("experimental_routing"):
        try:
            _ROUTER.add(entry.entry_id, policy)
        except (RuntimeError, ValueError) as error:
            raise ConfigEntryNotReady(
                "Proxy routing hook incompatible or targets conflicting"
            ) from error
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
    if entry.options.get("experimental_routing") and entry.options.get(
        "reload_targets"
    ):
        for entry_id in entry.options.get("targets", []):
            target = hass.config_entries.async_get_entry(entry_id)
            if target and target.domain != DOMAIN and not target.disabled_by:
                try:
                    await hass.config_entries.async_reload(entry_id)
                except Exception:  # noqa: BLE001 - isolate failures in third-party integrations
                    _LOGGER.warning(
                        "Selected integration could not be reloaded; restart HA if needed"
                    )


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        _ROUTER.remove(entry.entry_id)
        hass.data[DOMAIN].pop(entry.entry_id)
        return True
    return False
