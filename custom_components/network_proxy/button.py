from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [RefreshButton(hass.data[DOMAIN][entry.entry_id], entry.entry_id)]
    )


class RefreshButton(CoordinatorEntity, ButtonEntity):
    _attr_name = "Network Proxy Refresh Subscription"

    def __init__(self, runtime, entry_id):
        super().__init__(runtime["coordinator"])
        self.api = runtime["api"]
        self._attr_unique_id = f"{entry_id}_refresh"

    @property
    def available(self):
        return super().available and self.coordinator.data.get(
            "has_subscription", False
        )

    async def async_press(self):
        await self.api.refresh_subscription()
        await self.coordinator.async_request_refresh()
