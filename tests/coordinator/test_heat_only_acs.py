"""Coordinator side of #476: an AC in a heat only room with thermostats sits heating out."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roommind.const import MODE_HEATING

from .conftest import SAMPLE_ROOM, _create_coordinator, _make_store_mock, make_mock_states_get

AREA = "living_room_abc12345"
TRV = "climate.living_room"
AC = "climate.living_room_ac"
NEIGHBOUR_AC = "climate.bedroom_ac"

GROUP = {"id": "g1", "name": "Outdoor unit", "members": [AC, NEIGHBOUR_AC], "min_run_minutes": 15}


def _room(**overrides):
    room = {
        **SAMPLE_ROOM,
        "thermostats": [TRV],
        "acs": [AC],
        "devices": [
            {"entity_id": TRV, "type": "trv", "role": "auto", "heating_system_type": ""},
            {"entity_id": AC, "type": "ac", "role": "auto", "heating_system_type": ""},
        ],
        "climate_mode": "heat_only",
    }
    room.update(overrides)
    return room


def _ac_state():
    s = MagicMock()
    s.state = "off"
    s.attributes = {"hvac_modes": ["off", "heat", "cool"], "min_temp": 16.0, "max_temp": 30.0, "temperature": 21.0}
    return s


async def _run(hass, mock_config_entry, room):
    store = _make_store_mock({AREA: room}, settings={"compressor_groups": [GROUP]})
    hass.data = {"roommind": {"store": store}}
    base = make_mock_states_get(temp="18.0")
    ac = _ac_state()
    hass.states.get = MagicMock(side_effect=lambda eid: ac if eid == AC else base(eid))
    hass.services.async_call = AsyncMock()
    coordinator = _create_coordinator(hass, mock_config_entry)
    coordinator.data = await coordinator._async_update_data()
    return coordinator


def _ac_modes(hass):
    return [
        c.args[2].get("hvac_mode")
        for c in hass.services.async_call.call_args_list
        if c.args[1] == "set_hvac_mode" and c.args[2].get("entity_id") == AC
    ]


@pytest.mark.asyncio
async def test_heating_heat_only_room_keeps_the_ac_out(hass, mock_config_entry):
    coordinator = await _run(hass, mock_config_entry, _room())

    state = coordinator.data["rooms"][AREA]
    assert state["mode"] == MODE_HEATING
    assert "heat" not in _ac_modes(hass)
    # The outdoor unit never started, so it holds nobody to a minimum run.
    assert not coordinator._compressor_manager.check_must_stay_active(AC)
    assert state["compressor_protection_reason"] is None
    # The room's heating is no demand for the AC group.
    modes = coordinator._collect_member_room_modes(
        [AC, NEIGHBOUR_AC], coordinator.data["rooms"], {AREA: _room()}, {"climate_control_active": True}
    )
    assert modes == []


@pytest.mark.asyncio
async def test_heating_auto_room_still_uses_the_ac(hass, mock_config_entry):
    coordinator = await _run(hass, mock_config_entry, _room(climate_mode="auto"))

    assert coordinator.data["rooms"][AREA]["mode"] == MODE_HEATING
    assert "heat" in _ac_modes(hass)
    assert coordinator._compressor_manager.check_must_stay_active(AC)


@pytest.mark.asyncio
async def test_ac_left_running_keeps_its_minimum_run(hass, mock_config_entry):
    """Switching to heat only mid-run does not stop the outdoor unit before its minimum run."""
    room = _room(climate_mode="auto")
    coordinator = await _run(hass, mock_config_entry, room)
    assert coordinator._compressor_manager.check_must_stay_active(AC)

    room["climate_mode"] = "heat_only"
    hass.services.async_call.reset_mock()
    coordinator.data = await coordinator._async_update_data()

    assert "off" not in _ac_modes(hass)
    assert coordinator.data["rooms"][AREA]["compressor_protection_reason"] == "min_run"


@pytest.mark.asyncio
async def test_orchestration_still_routes_heat_only_heating_to_the_ac(hass, mock_config_entry):
    """Orchestration is the explicit choice to heat with the AC too, in heat only as well."""
    coordinator = await _run(hass, mock_config_entry, _room(heat_source_orchestration=True))

    state = coordinator.data["rooms"][AREA]
    assert state["active_heat_sources"] is not None
    assert "heat" in _ac_modes(hass)


@pytest.mark.asyncio
async def test_a_trv_in_a_boiler_group_still_counts_as_demand(hass, mock_config_entry):
    coordinator = await _run(hass, mock_config_entry, _room())

    modes = coordinator._collect_member_room_modes([TRV], coordinator.data["rooms"], {AREA: _room()}, {})
    assert modes == [MODE_HEATING]


@pytest.mark.asyncio
async def test_blocked_trv_idles_the_room_although_the_ac_is_free(hass, mock_config_entry):
    """The sitting-out AC must not keep the room in heating when its only heater is blocked."""
    coordinator = await _run(hass, mock_config_entry, _room())
    store = hass.data["roommind"]["store"]
    store.get_settings.return_value = {
        **store.get_settings.return_value,
        "compressor_groups": [GROUP, {"id": "g2", "name": "Boiler", "members": [TRV], "min_off_minutes": 30}],
    }
    coordinator._compressor_manager.load_groups(store.get_settings.return_value["compressor_groups"])
    coordinator._compressor_manager.update_member(TRV, False)
    with patch.object(coordinator._compressor_manager, "check_can_activate", lambda eid: eid != TRV):
        coordinator.data = await coordinator._async_update_data()

    assert coordinator.data["rooms"][AREA]["mode"] != MODE_HEATING


@pytest.mark.asyncio
async def test_device_setpoint_follows_the_direct_trv_alone(hass, mock_config_entry):
    room = _room()
    room["devices"][0]["setpoint_mode"] = "direct"
    coordinator = await _run(hass, mock_config_entry, room)

    state = coordinator.data["rooms"][AREA]
    assert state["mode"] == MODE_HEATING
    assert state["device_setpoint"] == state["heat_target"]
