"""Tests for the select platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.const import CLIMATE_MODES, DOMAIN
from custom_components.roommind.select import (
    RoomMindClimateModeSelect,
    _create_room_selects,
    async_setup_entry,
)


@pytest.fixture
def mock_select_coordinator():
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    store = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.hass.data = {DOMAIN: {"store": store}}
    return coordinator, store


def test_climate_mode_select_current_option(mock_select_coordinator):
    coordinator, store = mock_select_coordinator
    store.get_room.return_value = {"climate_mode": "heat_only"}
    select = RoomMindClimateModeSelect(coordinator, "living_room")
    assert select.current_option == "heat_only"


def test_climate_mode_select_defaults_to_auto(mock_select_coordinator):
    coordinator, store = mock_select_coordinator
    store.get_room.return_value = {}
    select = RoomMindClimateModeSelect(coordinator, "living_room")
    assert select.current_option == "auto"


def test_climate_mode_select_invalid_stored_mode_defaults_to_auto(mock_select_coordinator):
    coordinator, store = mock_select_coordinator
    store.get_room.return_value = {"climate_mode": "fan_only"}
    select = RoomMindClimateModeSelect(coordinator, "living_room")
    assert select.current_option == "auto"


def test_climate_mode_select_missing_room_defaults_to_auto(mock_select_coordinator):
    coordinator, store = mock_select_coordinator
    store.get_room.return_value = None
    select = RoomMindClimateModeSelect(coordinator, "living_room")
    assert select.current_option == "auto"


@pytest.mark.asyncio
async def test_climate_mode_select_select_option(mock_select_coordinator):
    coordinator, store = mock_select_coordinator
    store.async_update_room = AsyncMock()
    select = RoomMindClimateModeSelect(coordinator, "living_room")

    await select.async_select_option("cool_only")

    store.async_update_room.assert_awaited_once_with("living_room", {"climate_mode": "cool_only"})
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_climate_mode_select_rejects_invalid_option(mock_select_coordinator):
    coordinator, store = mock_select_coordinator
    store.async_update_room = AsyncMock()
    select = RoomMindClimateModeSelect(coordinator, "living_room")

    with pytest.raises(ValueError, match="Invalid climate mode"):
        await select.async_select_option("fan_only")

    store.async_update_room.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


def test_climate_mode_select_unique_id_entity_id_and_options(mock_select_coordinator):
    coordinator, _ = mock_select_coordinator
    select = RoomMindClimateModeSelect(coordinator, "living_room")
    assert select.unique_id == "roommind_living_room_climate_mode"
    assert select.entity_id == "select.roommind_living_room_climate_mode"
    assert select.options == CLIMATE_MODES


def test_create_room_selects(mock_select_coordinator):
    coordinator, _ = mock_select_coordinator
    entities = _create_room_selects(coordinator, "living_room")
    assert len(entities) == 1
    assert isinstance(entities[0], RoomMindClimateModeSelect)


@pytest.mark.asyncio
async def test_setup_entry_creates_selects(hass, mock_config_entry, store):
    await store.async_load()
    await store.async_save_room("room_a", {"thermostats": ["climate.trv1"]})
    await store.async_save_room("room_b", {"thermostats": ["climate.trv2"]})

    coordinator = MagicMock()
    coordinator._select_entity_areas = set()
    hass.data[DOMAIN] = {
        mock_config_entry.entry_id: coordinator,
        "store": store,
    }
    add_entities = MagicMock()

    await async_setup_entry(hass, mock_config_entry, add_entities)

    assert coordinator.async_add_select_entities is add_entities
    add_entities.assert_called_once()
    entities = add_entities.call_args[0][0]
    assert len(entities) == 2
    assert all(isinstance(entity, RoomMindClimateModeSelect) for entity in entities)
    assert coordinator._select_entity_areas == {"room_a", "room_b"}


@pytest.mark.asyncio
async def test_setup_entry_no_rooms(hass, mock_config_entry, store):
    await store.async_load()

    coordinator = MagicMock()
    coordinator._select_entity_areas = set()
    hass.data[DOMAIN] = {
        mock_config_entry.entry_id: coordinator,
        "store": store,
    }
    add_entities = MagicMock()

    await async_setup_entry(hass, mock_config_entry, add_entities)

    assert coordinator.async_add_select_entities is add_entities
    add_entities.assert_not_called()
