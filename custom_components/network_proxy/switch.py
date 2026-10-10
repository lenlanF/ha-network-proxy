"""Keep the manager running while independently controlling traffic proxying."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ProxySwitch(runtime)])


class ProxySwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "代理"
    _attr_icon = "mdi:shield-link-variant"

    def __init__(self, runtime):
        super().__init__(runtime["coordinator"])
        self.api = runtime["api"]
        self._attr_unique_id = runtime["entry"].entry_id + "_proxy_enabled"

    @property
    def is_on(self):
        return self.coordinator.data.get("proxy_enabled", True)

    async def async_turn_on(self, **_kwargs):
        await self.api.set_enabled(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **_kwargs):
        await self.api.set_enabled(False)
        await self.coordinator.async_request_refresh()
