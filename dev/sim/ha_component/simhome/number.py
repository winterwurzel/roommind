"""Valve opening/closing degree of TRVs with direct valve control (Sonoff TRVZB via Zigbee2MQTT)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from roommind_sim.devices import SimDevice

from .const import DOMAIN
from .entity import SimEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    rt = hass.data[DOMAIN]
    add(
        [
            SimValveNumber(rt, dev, kind, eid)
            for dev in rt.world.devices.values()
            for kind, eid in dev.spec.valve_numbers().items()
        ]
    )


def valve_device_info(dev: SimDevice) -> DeviceInfo:
    """One device for the TRV and its degree numbers, as Zigbee2MQTT creates it (the panel looks there)."""
    return DeviceInfo(identifiers={(DOMAIN, dev.entity_id)}, name=dev.spec.name, model=dev.spec.model)


class SimValveNumber(SimEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, rt: Any, dev: SimDevice, kind: str, entity_id: str) -> None:
        name = {"opening": "heating valve position", "closing": "idle valve position"}[kind]
        if not dev.spec.capabilities.get("valve_idle_position"):
            name = f"valve {kind} degree"
        super().__init__(rt, entity_id, f"{dev.spec.name} {name}")
        self.dev = dev
        self.kind = kind
        self._attr_device_info = valve_device_info(dev)

    @property
    def available(self) -> bool:
        return self.dev.state(self.now) != "unavailable"

    @property
    def native_value(self) -> float | None:
        return getattr(self.dev, f"valve_{self.kind}_deg")

    async def async_set_native_value(self, value: float) -> None:
        result = self.rt.world.valve_call(self.entity_id, value, self.now)
        self.rt.log_command(self.entity_id, "set_value", {"value": value}, result.status)
        self.async_write_ha_state()
        if not result.ok:
            raise HomeAssistantError(f"{self.entity_id}: {result.reason}")
