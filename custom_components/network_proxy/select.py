from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ProxySelect(runtime, entry.entry_id)])


class ProxySelect(CoordinatorEntity, SelectEntity):
    _attr_name = "Network Proxy Outlet"

    def __init__(self, runtime, entry_id):
        super().__init__(runtime["coordinator"])
        self.api = runtime["api"]
        self._attr_unique_id = f"{entry_id}_node"

    @property
    def options(self):
        return self.coordinator.data["options"]

    @property
    def current_option(self):
        return self.coordinator.data["current"]

    async def async_select_option(self, option):
        await self.api.select(option)
        await self.coordinator.async_request_refresh()
