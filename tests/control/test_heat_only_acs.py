"""Heat only keeps the ACs of a room with thermostats off (#476).

The mode promises "only uses thermostats, ACs stay off". Since heat-capable ACs
take part in heating (#23) they heated in heat_only rooms all the same. A room
with ACs only keeps heating with them, it has nothing else to heat with.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.roommind.control.mpc_controller import (
    MPCController,
    ac_heating_excluded,
    check_acs_can_heat,
    get_can_heat_cool,
)
from custom_components.roommind.control.thermal_model import RoomModelManager

from .conftest import build_hass, make_room

TRV = "climate.living_trv"
AC = "climate.hp"


def _state(state: str, modes: list[str], **attrs):
    s = MagicMock()
    s.state = state
    s.attributes = {"hvac_modes": modes, "min_temp": 16.0, "max_temp": 30.0, "temperature": 21.0, **attrs}
    return s


def _hass(ac_state="off"):
    hass = build_hass()
    states = {
        TRV: _state("off", ["heat", "off"], min_temp=5.0),
        AC: _state(ac_state, ["off", "heat", "cool", "heat_cool", "fan_only"]),
    }
    hass.states.get = MagicMock(side_effect=states.get)
    return hass


def _ctrl(hass, room, *, external=True):
    return MPCController(
        hass,
        room,
        model_manager=RoomModelManager(),
        outdoor_temp=5.0,
        settings={},
        has_external_sensor=external,
    )


def _calls(hass, eid, service=None):
    return [
        c[0][2]
        for c in hass.services.async_call.call_args_list
        if c[0][2].get("entity_id") == eid and (service is None or c[0][1] == service)
    ]


def _heats(hass, eid) -> bool:
    return any(c.get("hvac_mode") in ("heat", "heat_cool", "auto") for c in _calls(hass, eid, "set_hvac_mode"))


def test_excluded_only_in_heat_only_rooms_with_thermostats():
    assert ac_heating_excluded(make_room(climate_mode="heat_only", acs=[AC]))
    assert not ac_heating_excluded(make_room(climate_mode="auto", acs=[AC]))
    assert not ac_heating_excluded(make_room(climate_mode="heat_only", thermostats=[], acs=[AC]))


def test_orchestration_keeps_the_ac_as_heat_source():
    orchestrated = make_room(climate_mode="heat_only", acs=[AC], heat_source_orchestration=True)
    assert not ac_heating_excluded(orchestrated)
    # Without an external sensor orchestration does not run, so the flag changes nothing.
    assert ac_heating_excluded({**orchestrated, "temperature_sensor": ""})


def test_heat_only_room_with_thermostats_does_not_count_the_ac_as_heater():
    hass = _hass()
    mixed = make_room(climate_mode="heat_only", acs=[AC])
    assert not check_acs_can_heat(hass, mixed)
    assert check_acs_can_heat(hass, make_room(climate_mode="auto", acs=[AC]))
    assert check_acs_can_heat(hass, make_room(climate_mode="heat_only", thermostats=[], acs=[AC]))


def test_ac_only_heat_only_room_can_still_heat():
    hass = _hass()
    room = make_room(climate_mode="heat_only", thermostats=[], acs=[AC])
    can_heat, can_cool = get_can_heat_cool(room, acs_can_heat=check_acs_can_heat(hass, room))
    assert can_heat and not can_cool


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [True, False], ids=["full_control", "managed"])
async def test_heating_in_heat_only_room_leaves_the_ac_off(external):
    hass = _hass()
    ctrl = _ctrl(hass, make_room(climate_mode="heat_only", acs=[AC]), external=external)
    await ctrl.async_apply("heating", 21.0, power_fraction=1.0, current_temp=18.0)

    assert _heats(hass, TRV)
    assert not _heats(hass, AC)
    assert not _calls(hass, AC, "set_temperature")


@pytest.mark.asyncio
async def test_heating_switches_off_an_ac_left_heating():
    hass = _hass(ac_state="heat")
    ctrl = _ctrl(hass, make_room(climate_mode="heat_only", acs=[AC]))
    await ctrl.async_apply("heating", 21.0, power_fraction=1.0, current_temp=18.0)

    assert {"entity_id": AC, "hvac_mode": "off"} in _calls(hass, AC, "set_hvac_mode")


@pytest.mark.asyncio
async def test_heating_keeps_the_acs_idle_action():
    """An AC idling in fan_only must not toggle off/fan_only with every heating run."""
    hass = _hass(ac_state="fan_only")
    room = make_room(climate_mode="heat_only", acs=[AC])
    room["devices"][1]["idle_action"] = "fan_only"
    ctrl = _ctrl(hass, room)
    await ctrl.async_apply("heating", 21.0, power_fraction=1.0, current_temp=18.0)

    assert not any(c.get("hvac_mode") == "off" for c in _calls(hass, AC, "set_hvac_mode"))
    assert not _heats(hass, AC)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["heating", "idle"])
async def test_setback_ac_left_heating_ends_in_off(mode):
    """The setback keeps the current hvac mode, so a unit left in heat would go on heating."""
    hass = _hass(ac_state="heat")
    room = make_room(climate_mode="heat_only", acs=[AC])
    room["devices"][1]["idle_action"] = "setback"
    ctrl = _ctrl(hass, room)
    await ctrl.async_apply(mode, 21.0, power_fraction=1.0, current_temp=18.0)

    assert {"entity_id": AC, "hvac_mode": "off"} in _calls(hass, AC, "set_hvac_mode")


@pytest.mark.asyncio
async def test_ac_only_heat_only_room_heats_with_the_ac():
    hass = _hass()
    ctrl = _ctrl(hass, make_room(climate_mode="heat_only", thermostats=[], acs=[AC]))
    await ctrl.async_apply("heating", 21.0, power_fraction=1.0, current_temp=18.0)

    assert _heats(hass, AC)


@pytest.mark.asyncio
async def test_auto_room_still_heats_with_the_ac():
    hass = _hass()
    ctrl = _ctrl(hass, make_room(climate_mode="auto", acs=[AC]))
    await ctrl.async_apply("heating", 21.0, power_fraction=1.0, current_temp=18.0)

    assert _heats(hass, AC)
