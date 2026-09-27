"""Coordinator-level tests for the global boiler heating switch.

They run a full update cycle and check the climate service calls, so they cover
the coordinator gate and the per-device setpoint in async_apply as well as the
orchestrator decision.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roommind.coordinator import RoomMindCoordinator
from custom_components.roommind.store import RoomMindStore
from tests.managers.test_heat_source_integration import (
    DEFAULT_SETTINGS,
    ROOM_WITH_TRV_AND_AC,
    _calls_for_entity,
    _make_hass_states,
    _setup_store,
)


@pytest.fixture
def real_store(hass):
    s = RoomMindStore(hass)
    s._store = AsyncMock()
    s._store.async_load = AsyncMock(return_value=None)
    s._store.async_save = AsyncMock()
    return s


@pytest.fixture
def coordinator(hass, mock_config_entry, real_store):
    hass.data = {"roommind": {"store": real_store}}
    hass.services.async_call = AsyncMock()
    hass.states.get = MagicMock(side_effect=_make_hass_states())
    hass.config.latitude = 50.0
    hass.config.longitude = 10.0
    hass.config.units = MagicMock()
    hass.config.units.temperature_unit = "°C"
    with patch("homeassistant.helpers.frame.report_usage"):
        c = RoomMindCoordinator(hass, mock_config_entry)
    return c


TRV = "climate.trv_living"
AC = "climate.ac_living"
HEATER = "climate.heater_living"
BOILER_OFF = {**DEFAULT_SETTINGS, "boiler_heating_enabled": False}


def _room(devices, **overrides):
    room = {**ROOM_WITH_TRV_AND_AC, "devices": devices, **overrides}
    return room


def _trv(**extra):
    return {"entity_id": TRV, "type": "trv", "role": "auto", "heating_system_type": "", **extra}


AC_DEVICE = {"entity_id": AC, "type": "ac", "role": "auto", "heating_system_type": ""}
HEATER_DEVICE = {"entity_id": HEATER, "type": "electric", "role": "auto", "setpoint_mode": "direct"}
HEATER_STATE = {HEATER: ("off", {"hvac_modes": ["off", "heat"], "hvac_action": "off"})}


def _modes(hass, entity_id):
    return [d["hvac_mode"] for svc, d in _calls_for_entity(hass, entity_id) if svc == "set_hvac_mode"]


def _temps(hass, entity_id):
    return [d["temperature"] for svc, d in _calls_for_entity(hass, entity_id) if svc == "set_temperature"]


class TestBoilerDisabled:
    @pytest.mark.asyncio
    async def test_no_surplus_turns_trv_and_ac_off(self, coordinator, real_store, hass):
        """Above the floor and without prefer-electric nothing may heat."""
        await _setup_store(real_store, _room([_trv(), AC_DEVICE]), BOILER_OFF)
        hass.states.get = MagicMock(side_effect=_make_hass_states(temp="20.0", outdoor_temp="5.0"))

        await coordinator._async_update_data()

        assert "heat" not in _modes(hass, TRV)
        assert "heat" not in _modes(hass, AC)
        assert coordinator._heat_source_states.get("living_room") == "none"

    @pytest.mark.asyncio
    async def test_cold_room_trv_heats_to_floor_only(self, coordinator, real_store, hass):
        """Below eco the TRV heats, with its setpoint at eco, not the 21 C target."""
        room = _room([_trv(setpoint_mode="direct"), AC_DEVICE])
        await _setup_store(real_store, room, BOILER_OFF)
        hass.states.get = MagicMock(side_effect=_make_hass_states(temp="16.0", outdoor_temp="5.0"))

        await coordinator._async_update_data()

        assert _modes(hass, TRV) == ["heat"]
        assert _temps(hass, TRV) == [17.0]
        assert "heat" not in _modes(hass, AC)

    @pytest.mark.asyncio
    async def test_boiler_only_room_trv_turned_off(self, coordinator, real_store, hass):
        """A TRV-only room without the orchestration flag is covered as well."""
        room = _room([_trv()], heat_source_orchestration=False)
        await _setup_store(real_store, room, BOILER_OFF)
        hass.states.get = MagicMock(side_effect=_make_hass_states(temp="20.0", outdoor_temp="5.0"))

        await coordinator._async_update_data()

        assert "heat" not in _modes(hass, TRV)


class TestElectricWithoutAc:
    @pytest.mark.asyncio
    async def test_prefer_electric_runs_heater_in_room_without_ac(self, coordinator, real_store, hass):
        """Regression: rooms with TRV + electric heater but no AC were never orchestrated,
        so the heater was never commanded even with prefer-electric on."""
        room = _room([_trv(), HEATER_DEVICE], prefer_electric_heat=True)
        await _setup_store(real_store, room)
        hass.states.get = MagicMock(side_effect=_make_hass_states(temp="20.5", outdoor_temp="5.0", extra=HEATER_STATE))

        await coordinator._async_update_data()

        assert _modes(hass, HEATER) == ["heat"]
        assert "heat" not in _modes(hass, TRV)
        assert coordinator._heat_source_states.get("living_room") == "secondary"
