"""cool_only rooms configured with a single comfort_temp/eco_temp (API, old configs).

The single value landed on the heat side next to the default comfort_cool (24.0), e.g.
heat 24.5 / cool 24.0: the optimizer lifted the cool target to 24.5 and the cover
target took 24.5, while guard, hysteresis and the panel used 24.0.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.const import DEFAULT_COMFORT_COOL, DEFAULT_ECO_COOL, TargetTemps
from custom_components.roommind.utils.schedule_utils import cap_cool_only_heat, make_target_resolver

from .coordinator.conftest import SAMPLE_ROOM, _create_coordinator, _make_store_mock, make_mock_states_get


@pytest.mark.parametrize(
    ("heat", "cool", "mode", "expected"),
    [
        (24.5, 24.0, "cool_only", (24.0, 24.0)),
        (21.0, 24.0, "cool_only", (21.0, 24.0)),
        (None, 24.0, "cool_only", (None, 24.0)),
        (24.5, None, "cool_only", (24.5, None)),
        (24.5, 24.0, "auto", (24.5, 24.0)),
        (24.5, 24.0, "heat_only", (24.5, 24.0)),
    ],
)
def test_cap_cool_only_heat(heat, cool, mode, expected):
    assert cap_cool_only_heat(heat, cool, mode) == expected


@pytest.mark.asyncio
async def test_api_create_with_single_temps_sets_the_cool_side(store):
    room = await store.async_save_room("buero", {"climate_mode": "cool_only", "comfort_temp": 24.5, "eco_temp": 28.0})
    assert room["comfort_cool"] == 24.5
    assert room["eco_cool"] == 28.0


@pytest.mark.asyncio
async def test_api_update_with_single_temps_sets_the_cool_side(store):
    await store.async_save_room("buero", {"climate_mode": "cool_only", "comfort_cool": 23.0})
    room = await store.async_save_room("buero", {"comfort_temp": 25.0})
    assert room["comfort_cool"] == 25.0


@pytest.mark.asyncio
async def test_panel_values_and_other_modes_are_untouched(store):
    panel = await store.async_save_room(
        "buero", {"climate_mode": "cool_only", "comfort_temp": 21.0, "comfort_cool": 24.5}
    )
    assert panel["comfort_cool"] == 24.5
    auto = await store.async_save_room("wohnzimmer", {"climate_mode": "auto", "comfort_temp": 22.0})
    assert auto["comfort_heat"] == 22.0
    assert auto["comfort_cool"] == DEFAULT_COMFORT_COOL
    heat = await store.async_save_room("bad", {"climate_mode": "heat_only", "eco_temp": 18.0})
    assert heat["eco_cool"] == DEFAULT_ECO_COOL


@pytest.mark.asyncio
async def test_old_cool_only_room_without_split_fields_migrates_to_the_cool_side(store):
    store._store.async_load = AsyncMock(
        return_value={
            "rooms": {
                "buero": {
                    "area_id": "buero",
                    "climate_mode": "cool_only",
                    "comfort_temp": 24.5,
                    "eco_temp": 28.0,
                    "thermostats": [],
                    "acs": [],
                    "schedules": [],
                }
            }
        }
    )
    await store.async_load()
    room = store.get_room("buero")
    assert room["comfort_cool"] == 24.5
    assert room["eco_cool"] == 28.0


def test_resolver_series_never_inverts_a_cool_only_band():
    room = {"climate_mode": "cool_only", "comfort_heat": 24.5, "comfort_cool": 24.0}
    assert make_target_resolver(None, room, {})(0.0) == TargetTemps(heat=24.0, cool=24.0)
    auto = {**room, "climate_mode": "auto"}
    assert make_target_resolver(None, auto, {})(0.0) == TargetTemps(heat=24.5, cool=24.0)


@pytest.mark.asyncio
async def test_coordinator_resolves_a_consistent_cool_only_band(hass, mock_config_entry):
    room = {
        **SAMPLE_ROOM,
        "climate_mode": "cool_only",
        "thermostats": [],
        "acs": ["climate.living_room"],
        "devices": [{"entity_id": "climate.living_room", "type": "ac", "role": "auto", "heating_system_type": ""}],
        "schedules": [],
        "comfort_heat": 24.5,
        "comfort_temp": 24.5,
        "comfort_cool": 24.0,
    }
    store = _make_store_mock({"living_room_abc12345": room})
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(side_effect=make_mock_states_get(temp="25.0"))
    hass.services.async_call = AsyncMock()
    coordinator = _create_coordinator(hass, mock_config_entry)
    data = await coordinator._async_update_data()
    state = data["rooms"]["living_room_abc12345"]
    assert (state["heat_target"], state["cool_target"], state["target_temp"]) == (24.0, 24.0, 24.0)
