"""Connection, persistent source settings, discovery and target selection UI."""

from urllib.parse import urlparse

import voluptuous as vol
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Controller, Manager
from .const import DOMAIN

CONNECTION_DEFAULTS = {
    "controller_url": "http://127.0.0.1:9090",
    "manager_url": "http://127.0.0.1:9091",
    "secret": "",
    "proxy_url": "http://127.0.0.1:7890",
}


def password_selector():
    return selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    )


def connection_schema(values):
    return vol.Schema(
        {
            vol.Required(key, default=values.get(key, default)): password_selector()
            if key == "secret"
            else str
            for key, default in CONNECTION_DEFAULTS.items()
        }
    )


def validate_connection(values):
    for key in ("controller_url", "manager_url", "proxy_url"):
        parsed = urlparse(values[key])
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("Invalid endpoint")
        _ = parsed.port
    if len(values["secret"]) < 16:
        raise ValueError("Invalid secret")


async def validate_controller(hass, values):
    validate_connection(values)
    session = async_get_clientsession(hass)
    await Controller(session, values["controller_url"], values["secret"]).status()
    manager = Manager(session, values["manager_url"], values["secret"])
    health = await manager.health()
    if tuple(int(item) for item in health["version"].split(".")) < (0, 4, 0):
        raise ValueError("Update the application to 0.4.0 first")
    await manager.status()


class ProxyFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ProxyOptionsFlow()

    async def async_step_user(self, user_input=None):
        return await self.async_step_controller(user_input)

    async def async_step_controller(self, user_input=None):
        errors = {}
        if user_input:
            try:
                await validate_controller(self.hass, user_input)
            except (ClientError, TimeoutError, ValueError, KeyError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input["controller_url"].rstrip("/"))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Network Proxy", data=user_input)
        return self.async_show_form(
            step_id="controller",
            data_schema=connection_schema(user_input or {}),
            errors=errors,
        )


class ProxyOptionsFlow(config_entries.OptionsFlow):
    def values(self):
        return {**self.config_entry.data, **self.config_entry.options}

    def save(self, changes):
        return self.async_create_entry(
            title="", data={**self.config_entry.options, **changes}
        )

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["application", "connection"],
        )

    async def async_step_connection(self, user_input=None):
        errors = {}
        if user_input:
            try:
                await validate_controller(self.hass, user_input)
                if user_input["controller_url"].rstrip("/") != self.config_entry.data[
                    "controller_url"
                ].rstrip("/"):
                    raise ValueError("Re-add to change controller identity")
            except (ClientError, TimeoutError, ValueError, KeyError):
                errors["base"] = "cannot_connect"
            else:
                return self.save(user_input)
        return self.async_show_form(
            step_id="connection",
            data_schema=connection_schema(user_input or self.values()),
            errors=errors,
        )

    async def async_step_application(self, user_input=None):
        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(step_id="application", data_schema=vol.Schema({}))
