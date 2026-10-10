from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            VersionSensor(runtime["coordinator"], entry.entry_id),
            RoutingSensor(runtime, entry.entry_id),
        ]
    )


class VersionSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Network Proxy Version"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_version"

    @property
    def native_value(self):
        return self.coordinator.data["version"]


class RoutingSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Network Proxy Captured Requests"
    _attr_native_unit_of_measurement = "requests"

    def __init__(self, runtime, entry_id):
        super().__init__(runtime["coordinator"])
        self.policy = runtime["policy"]
        self.config = runtime["config"]
        self._attr_unique_id = f"{entry_id}_requests"

    @property
    def native_value(self):
        return sum(self.policy.attempts.values())

    @property
    def extra_state_attributes(self):
        return {
            "experimental_routing": bool(self.policy.targets),
            "coverage": "aiohttp requests with HA config-entry task context only",
            "counts_reset_on_reload": True,
            "targets": {
                target: {
                    "attempts": self.policy.attempts[target],
                    "responses": self.policy.responses[target],
                    "failures": self.policy.failures[target],
                    "skipped": self.policy.skipped[target],
                }
                for target in sorted(self.policy.targets)
            },
        }
