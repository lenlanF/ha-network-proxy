"""Explicit opt-in requests for integrations, without patching shared sessions."""

from aiohttp import BasicAuth, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN


def async_request(hass, entry_id, method, url, **kwargs):
    data = hass.data[DOMAIN][entry_id]["config"]
    kwargs["proxy"] = data["proxy_url"]
    if data.get("proxy_username"):
        kwargs["proxy_auth"] = BasicAuth(
            data["proxy_username"], data.get("proxy_password", "")
        )
    kwargs.setdefault("timeout", ClientTimeout(total=30))
    return async_get_clientsession(hass).request(method, url, **kwargs)
