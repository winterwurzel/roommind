"""Select platform for RoomMind."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CLIMATE_MODE_AUTO, CLIMATE_MODES, DOMAIN
from .coordinator import RoomMindCoordinator


def _create_room_selects(
    coordinator: RoomMindCoordinator,
    area_id: str,
) -> list[SelectEntity]:
    """Create select entities for a room."""
    return [RoomMindClimateModeSelect(coordinator, area_id)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RoomMind select entities from a config entry."""
    coordinator: RoomMindCoordinator = hass.data[DOMAIN][entry.entry_id]
    store = hass.data[DOMAIN]["store"]
    coordinator.async_add_select_entities = async_add_entities

    rooms = store.get_rooms()
    entities: list[SelectEntity] = []
    for area_id in rooms:
        entities.extend(_create_room_selects(coordinator, area_id))
        coordinator._select_entity_areas.add(area_id)
    if entities:
        async_add_entities(entities)


class RoomMindClimateModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for room climate mode."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:home-thermometer-outline"
    _attr_options = CLIMATE_MODES

    def __init__(self, coordinator: RoomMindCoordinator, area_id: str) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        self._attr_unique_id = f"{DOMAIN}_{area_id}_climate_mode"
        self._attr_name = f"{area_id} Climate Mode"
        self.entity_id = f"select.{DOMAIN}_{area_id}_climate_mode"

    @property
    def current_option(self) -> str:
        """Return the current climate mode for the room."""
        store = self.coordinator.hass.data[DOMAIN]["store"]
        room = store.get_room(self._area_id)
        if not room:
            return CLIMATE_MODE_AUTO
        mode = room.get("climate_mode", CLIMATE_MODE_AUTO)
        return mode if mode in CLIMATE_MODES else CLIMATE_MODE_AUTO

    async def async_select_option(self, option: str) -> None:
        """Set the room climate mode."""
        if option not in CLIMATE_MODES:
            raise ValueError(f"Invalid climate mode: {option}")

        store = self.coordinator.hass.data[DOMAIN]["store"]
        await store.async_update_room(self._area_id, {"climate_mode": option})
        await self.coordinator.async_request_refresh()
