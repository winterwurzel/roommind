"""Tests for learn-only mode (climate_control_active=False)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from .conftest import (
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    make_mock_states_get,
)


class TestClimateControlDisabled:
    """Tests for learn-only mode (climate_control_active=False)."""

    @pytest.mark.asyncio
    async def test_no_service_calls_when_climate_control_disabled(self, hass, mock_config_entry):
        """When climate_control_active is False, no climate service calls are made."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(temp="18.0", humidity="55.0"),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        # No climate.* service calls at all
        climate_calls = [c for c in hass.services.async_call.call_args_list if c[0][0] == "climate"]
        assert climate_calls == [], f"Expected no climate calls, got {climate_calls}"

    @pytest.mark.asyncio
    async def test_mode_is_idle_when_climate_control_disabled(self, hass, mock_config_entry):
        """Room mode should be idle when climate control is disabled."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(temp="18.0", humidity="55.0"),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room = data["rooms"]["living_room_abc12345"]
        assert room["mode"] == "idle"

    @pytest.mark.asyncio
    async def test_observed_heating_trains_ekf_as_heating(self, hass, mock_config_entry):
        """When device is self-regulating (hvac_action=heating), EKF should train as heating."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                temp="18.0",
                humidity="55.0",
                extra={
                    "climate.living_room": (
                        "heat",
                        {"hvac_action": "heating", "current_temperature": 18},
                    ),
                },
            ),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        # Run 13 cycles: 6 to first EKF flush (init only), 6 more to second flush
        # (actual training), plus 1 extra for safety.
        for _ in range(13):
            data = await coordinator._async_update_data()

        # Display mode reflects observed device state (#36)
        room = data["rooms"]["living_room_abc12345"]
        assert room["mode"] == "heating"
        assert room["heating_power"] == 100

        # EKF should have trained with heating mode
        n_idle, n_heating, n_cooling = coordinator._model_manager.get_mode_counts(
            "living_room_abc12345",
        )
        assert n_heating > 0, "EKF should have trained with heating observations"

    @pytest.mark.asyncio
    async def test_missing_hvac_action_uses_inferred_mode(self, hass, mock_config_entry):
        """Device in heat mode without hvac_action -> training uses inferred mode (#69)."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        # Device in heat mode, current_temp (18) < setpoint (30) -> inferred heating
        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                temp="18.0",
                humidity="55.0",
                extra={
                    "climate.living_room": (
                        "heat",
                        {"current_temperature": 18, "temperature": 30},
                    ),
                },
            ),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        for _ in range(13):
            await coordinator._async_update_data()

        n_idle, n_heating, n_cooling = coordinator._model_manager.get_mode_counts(
            "living_room_abc12345",
        )
        assert n_heating > 0, "Training should use inferred heating when hvac_action is missing"

    @pytest.mark.asyncio
    async def test_inferred_heating_does_not_affect_previous_modes(self, hass, mock_config_entry):
        """Display mode (inferred heating) must not contaminate _previous_modes."""
        room = {**SAMPLE_ROOM, "heating_system_type": "radiator"}
        store = _make_store_mock({"living_room_abc12345": room})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        # Thermostat in heat mode, no hvac_action -> inferred heating for display
        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                temp="18.0",
                humidity="55.0",
                extra={
                    "climate.living_room": (
                        "heat",
                        {"current_temperature": 18, "temperature": 30},
                    ),
                },
            ),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_data = data["rooms"]["living_room_abc12345"]
        # Display should show heating (inferred from hvac_mode + setpoint)
        assert room_data["mode"] == "heating"
        # Internal _previous_modes must stay idle (no side effects)
        assert coordinator._previous_modes.get("living_room_abc12345") == "idle"
        # Residual heat tracking must not be triggered
        assert "living_room_abc12345" not in coordinator._residual_tracker._on_since

    @pytest.mark.asyncio
    async def test_device_off_trains_as_idle(self, hass, mock_config_entry):
        """Device explicitly off -> EKF trains as idle."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                temp="18.0",
                humidity="55.0",
                extra={
                    "climate.living_room": (
                        "off",
                        {"current_temperature": 18},
                    ),
                },
            ),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        for _ in range(13):
            await coordinator._async_update_data()

        n_idle, n_heating, n_cooling = coordinator._model_manager.get_mode_counts(
            "living_room_abc12345",
        )
        assert n_idle > 0, "EKF should have trained with idle observations"
        assert n_heating == 0

    @pytest.mark.asyncio
    async def test_observed_cooling_trains_ekf_as_cooling(self, hass, mock_config_entry):
        """AC with hvac_action=cooling -> EKF trains as cooling."""
        room_with_ac = {
            **SAMPLE_ROOM,
            "thermostats": [],
            "acs": ["climate.living_room_ac"],
            "devices": [
                {"entity_id": "climate.living_room", "type": "trv", "role": "auto", "heating_system_type": ""},
                {"entity_id": "climate.living_room_ac", "type": "ac", "role": "auto", "heating_system_type": ""},
            ],
            "climate_mode": "cool_only",
        }
        store = _make_store_mock({"living_room_abc12345": room_with_ac})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                temp="25.0",
                humidity="55.0",
                extra={
                    "climate.living_room_ac": (
                        "cool",
                        {"hvac_action": "cooling", "current_temperature": 25, "hvac_modes": ["cool", "off"]},
                    ),
                },
            ),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        for _ in range(13):
            await coordinator._async_update_data()

        n_idle, n_heating, n_cooling = coordinator._model_manager.get_mode_counts(
            "living_room_abc12345",
        )
        assert n_cooling > 0, "EKF should have trained with cooling observations"

    @pytest.mark.asyncio
    async def test_conflicting_actions_skip_training(self, hass, mock_config_entry):
        """Thermostat heating + AC cooling simultaneously -> skip training."""
        room_both = {
            **SAMPLE_ROOM,
            "thermostats": ["climate.living_room"],
            "acs": ["climate.living_room_ac"],
            "devices": [
                {"entity_id": "climate.living_room", "type": "trv", "role": "auto", "heating_system_type": ""},
                {"entity_id": "climate.living_room_ac", "type": "ac", "role": "auto", "heating_system_type": ""},
            ],
        }
        store = _make_store_mock({"living_room_abc12345": room_both})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                temp="20.0",
                humidity="55.0",
                extra={
                    "climate.living_room": (
                        "heat",
                        {"hvac_action": "heating", "current_temperature": 20},
                    ),
                    "climate.living_room_ac": (
                        "cool",
                        {"hvac_action": "cooling", "current_temperature": 20, "hvac_modes": ["cool", "off"]},
                    ),
                },
            ),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        for _ in range(13):
            await coordinator._async_update_data()

        n_idle, n_heating, n_cooling = coordinator._model_manager.get_mode_counts(
            "living_room_abc12345",
        )
        assert n_idle == 0 and n_heating == 0 and n_cooling == 0, "No training when actions conflict"

    @pytest.mark.asyncio
    async def test_unavailable_device_skips_training(self, hass, mock_config_entry):
        """Unavailable climate device -> skip training."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                temp="18.0",
                humidity="55.0",
                extra={
                    "climate.living_room": ("unavailable", {}),
                },
            ),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        for _ in range(13):
            await coordinator._async_update_data()

        n_idle, n_heating, n_cooling = coordinator._model_manager.get_mode_counts(
            "living_room_abc12345",
        )
        assert n_idle == 0 and n_heating == 0 and n_cooling == 0

    @pytest.mark.asyncio
    async def test_observe_device_action_unit(self, hass, mock_config_entry):
        """Unit test for _observe_device_action with various device states."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        hass.data = {"roommind": {"store": store}}
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        room = SAMPLE_ROOM

        # Device off -> idle
        s = MagicMock()
        s.state = "off"
        s.attributes = {}
        hass.states.get = MagicMock(return_value=s)
        assert coordinator._observe_device_action(room) == ("idle", 0.0)

        # Device heating via hvac_action
        s = MagicMock()
        s.state = "heat"
        s.attributes = {"hvac_action": "heating"}
        hass.states.get = MagicMock(return_value=s)
        assert coordinator._observe_device_action(room) == ("heating", 1.0)

        # Device cooling via hvac_action
        s = MagicMock()
        s.state = "cool"
        s.attributes = {"hvac_action": "cooling"}
        hass.states.get = MagicMock(return_value=s)
        assert coordinator._observe_device_action(room) == ("cooling", 1.0)

        # Device in heat mode, hvac_action idle
        s = MagicMock()
        s.state = "heat"
        s.attributes = {"hvac_action": "idle"}
        hass.states.get = MagicMock(return_value=s)
        assert coordinator._observe_device_action(room) == ("idle", 0.0)

        # Device in heat mode, no hvac_action -> unobservable
        s = MagicMock()
        s.state = "heat"
        s.attributes = {}
        hass.states.get = MagicMock(return_value=s)
        assert coordinator._observe_device_action(room) == (None, 0.0)

        # Device unavailable -> unobservable
        s = MagicMock()
        s.state = "unavailable"
        s.attributes = {}
        hass.states.get = MagicMock(return_value=s)
        assert coordinator._observe_device_action(room) == (None, 0.0)

        # Device drying -> unobservable (unknown thermal effect)
        s = MagicMock()
        s.state = "dry"
        s.attributes = {"hvac_action": "drying"}
        hass.states.get = MagicMock(return_value=s)
        assert coordinator._observe_device_action(room) == (None, 0.0)

        # Device not found -> unobservable
        hass.states.get = MagicMock(return_value=None)
        assert coordinator._observe_device_action(room) == (None, 0.0)

        # Preheating -> treated as heating
        s = MagicMock()
        s.state = "heat"
        s.attributes = {"hvac_action": "preheating"}
        hass.states.get = MagicMock(return_value=s)
        assert coordinator._observe_device_action(room) == ("heating", 1.0)


class TestCoverageGaps:
    """Tests covering uncovered coordinator lines."""

    @pytest.mark.asyncio
    async def test_climate_control_disabled_observe_device(self, hass, mock_config_entry):
        """When climate control is disabled, device state is observed for display."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        device_state = MagicMock()
        device_state.state = "heat"
        device_state.attributes = {
            "hvac_action": "heating",
            "current_temperature": 18.0,
            "temperature": 21.0,
            "hvac_modes": ["off", "heat"],
        }
        base_mock = make_mock_states_get()

        def custom_get(eid):
            if eid == "climate.living_room":
                return device_state
            return base_mock(eid)

        hass.states.get = MagicMock(side_effect=custom_get)
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room = data["rooms"]["living_room_abc12345"]
        # Display should show observed heating
        assert room["mode"] == "heating"
        # But no climate service calls should have been made for this room's control
        climate_calls = [c for c in hass.services.async_call.call_args_list if c[0][0] == "climate"]
        assert len(climate_calls) == 0

    @pytest.mark.asyncio
    async def test_climate_control_disabled_no_hvac_action_infers_mode(self, hass, mock_config_entry):
        """When control disabled and no hvac_action, mode is inferred from hvac_mode."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        device_state = MagicMock()
        device_state.state = "heat"
        device_state.attributes = {
            "current_temperature": 18.0,
            "temperature": 21.0,
            "hvac_modes": ["off", "heat"],
        }
        base_mock = make_mock_states_get()

        def custom_get(eid):
            if eid == "climate.living_room":
                return device_state
            return base_mock(eid)

        hass.states.get = MagicMock(side_effect=custom_get)
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room = data["rooms"]["living_room_abc12345"]
        # _infer_device_mode: heat mode, current < setpoint -> heating
        assert room["mode"] == "heating"

    @pytest.mark.asyncio
    async def test_infer_device_mode_at_setpoint_idle(self, hass, mock_config_entry):
        """_infer_device_mode returns idle when device is at setpoint."""
        coordinator = _create_coordinator(hass, mock_config_entry)

        device_state = MagicMock()
        device_state.state = "heat"
        device_state.attributes = {
            "current_temperature": 21.0,
            "temperature": 21.0,
        }
        hass.states.get = MagicMock(return_value=device_state)

        result = coordinator._infer_device_mode(
            {
                "thermostats": ["climate.trv1"],
                "acs": [],
                "devices": [{"entity_id": "climate.trv1", "type": "trv", "role": "auto", "heating_system_type": ""}],
            }
        )
        assert result == "idle"

    @pytest.mark.asyncio
    async def test_infer_device_mode_cooling_at_setpoint(self, hass, mock_config_entry):
        """_infer_device_mode returns idle when AC is at/below setpoint."""
        coordinator = _create_coordinator(hass, mock_config_entry)

        device_state = MagicMock()
        device_state.state = "cool"
        device_state.attributes = {
            "current_temperature": 23.0,
            "temperature": 24.0,
        }
        hass.states.get = MagicMock(return_value=device_state)

        result = coordinator._infer_device_mode(
            {
                "thermostats": [],
                "acs": ["climate.ac1"],
                "devices": [{"entity_id": "climate.ac1", "type": "ac", "role": "auto", "heating_system_type": ""}],
            }
        )
        assert result == "idle"

    @pytest.mark.asyncio
    async def test_infer_device_mode_cooling_above_setpoint(self, hass, mock_config_entry):
        """_infer_device_mode returns cooling when AC is above setpoint."""
        coordinator = _create_coordinator(hass, mock_config_entry)

        device_state = MagicMock()
        device_state.state = "cool"
        device_state.attributes = {
            "current_temperature": 26.0,
            "temperature": 24.0,
        }
        hass.states.get = MagicMock(return_value=device_state)

        result = coordinator._infer_device_mode(
            {
                "thermostats": [],
                "acs": ["climate.ac1"],
                "devices": [{"entity_id": "climate.ac1", "type": "ac", "role": "auto", "heating_system_type": ""}],
            }
        )
        assert result == "cooling"

    @pytest.mark.asyncio
    async def test_infer_device_mode_heat_cool_above_high_cools(self, hass, mock_config_entry):
        """heat_cool AC above the high setpoint infers cooling (#332)."""
        coordinator = _create_coordinator(hass, mock_config_entry)

        device_state = MagicMock()
        device_state.state = "heat_cool"
        device_state.attributes = {
            "current_temperature": 26.0,
            "target_temp_low": 20.0,
            "target_temp_high": 24.0,
        }
        hass.states.get = MagicMock(return_value=device_state)

        result = coordinator._infer_device_mode(
            {
                "thermostats": [],
                "acs": ["climate.ac1"],
                "devices": [{"entity_id": "climate.ac1", "type": "ac", "role": "auto", "heating_system_type": ""}],
            }
        )
        assert result == "cooling"

    @pytest.mark.asyncio
    async def test_infer_device_mode_heat_cool_below_low_heats(self, hass, mock_config_entry):
        """heat_cool device below the low setpoint infers heating (#332)."""
        coordinator = _create_coordinator(hass, mock_config_entry)

        device_state = MagicMock()
        device_state.state = "heat_cool"
        device_state.attributes = {
            "current_temperature": 18.0,
            "target_temp_low": 20.0,
            "target_temp_high": 24.0,
        }
        hass.states.get = MagicMock(return_value=device_state)

        result = coordinator._infer_device_mode(
            {
                "thermostats": [],
                "acs": ["climate.ac1"],
                "devices": [{"entity_id": "climate.ac1", "type": "ac", "role": "auto", "heating_system_type": ""}],
            }
        )
        assert result == "heating"

    @pytest.mark.asyncio
    async def test_infer_device_mode_heat_cool_within_band_idle(self, hass, mock_config_entry):
        """heat_cool device inside the dead-band infers idle (#332)."""
        coordinator = _create_coordinator(hass, mock_config_entry)

        device_state = MagicMock()
        device_state.state = "heat_cool"
        device_state.attributes = {
            "current_temperature": 22.0,
            "target_temp_low": 20.0,
            "target_temp_high": 24.0,
        }
        hass.states.get = MagicMock(return_value=device_state)

        result = coordinator._infer_device_mode(
            {
                "thermostats": [],
                "acs": ["climate.ac1"],
                "devices": [{"entity_id": "climate.ac1", "type": "ac", "role": "auto", "heating_system_type": ""}],
            }
        )
        assert result == "idle"

    @pytest.mark.asyncio
    async def test_infer_device_mode_auto_range_cools(self, hass, mock_config_entry):
        """auto mode with range setpoints infers cooling above high (#332)."""
        coordinator = _create_coordinator(hass, mock_config_entry)

        device_state = MagicMock()
        device_state.state = "auto"
        device_state.attributes = {
            "current_temperature": 27.0,
            "target_temp_low": 19.0,
            "target_temp_high": 23.0,
        }
        hass.states.get = MagicMock(return_value=device_state)

        result = coordinator._infer_device_mode(
            {
                "thermostats": [],
                "acs": ["climate.ac1"],
                "devices": [{"entity_id": "climate.ac1", "type": "ac", "role": "auto", "heating_system_type": ""}],
            }
        )
        assert result == "cooling"

    @pytest.mark.asyncio
    async def test_infer_device_mode_auto_without_range_idle(self, hass, mock_config_entry):
        """auto mode without explicit low/high stays idle (conservative, #332)."""
        coordinator = _create_coordinator(hass, mock_config_entry)

        device_state = MagicMock()
        device_state.state = "auto"
        device_state.attributes = {
            "current_temperature": 27.0,
            "temperature": 23.0,  # single setpoint only — ambiguous direction
        }
        hass.states.get = MagicMock(return_value=device_state)

        result = coordinator._infer_device_mode(
            {
                "thermostats": [],
                "acs": ["climate.ac1"],
                "devices": [{"entity_id": "climate.ac1", "type": "ac", "role": "auto", "heating_system_type": ""}],
            }
        )
        assert result == "idle"

    @pytest.mark.asyncio
    async def test_climate_off_heat_cool_ac_no_action_displays_cooling(self, hass, mock_config_entry):
        """#332: climate off + AC in heat_cool without hvac_action -> cooling shown/recorded."""
        room_with_ac = {
            **SAMPLE_ROOM,
            "thermostats": [],
            "acs": ["climate.bedroom_ac"],
            "devices": [
                {"entity_id": "climate.bedroom_ac", "type": "ac", "role": "auto", "heating_system_type": ""},
            ],
        }
        store = _make_store_mock({"living_room_abc12345": room_with_ac})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        # heat_cool AC, room 26 above high setpoint, NO hvac_action attribute
        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                temp="26.0",
                humidity="55.0",
                outdoor_temp="30.0",
                extra={
                    "climate.bedroom_ac": (
                        "heat_cool",
                        {
                            "current_temperature": 26.0,
                            "target_temp_low": 20.0,
                            "target_temp_high": 23.0,
                            "hvac_modes": ["heat_cool", "off"],
                        },
                    ),
                },
            ),
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        for _ in range(13):
            data = await coordinator._async_update_data()

        room = data["rooms"]["living_room_abc12345"]
        assert room["mode"] == "cooling"
        assert room["heating_power"] == 100

        # EKF should have trained with cooling observations, not idle
        n_idle, n_heating, n_cooling = coordinator._model_manager.get_mode_counts(
            "living_room_abc12345",
        )
        assert n_cooling > 0, "EKF should learn from the self-regulating cooling device"

    @pytest.mark.asyncio
    async def test_observe_device_conflicting_actions(self, hass, mock_config_entry):
        """Conflicting device actions returns None (unobservable)."""
        coordinator = _create_coordinator(hass, mock_config_entry)

        def mock_get(eid):
            s = MagicMock()
            if eid == "climate.trv1":
                s.state = "heat"
                s.attributes = {"hvac_action": "heating"}
            elif eid == "climate.ac1":
                s.state = "cool"
                s.attributes = {"hvac_action": "cooling"}
            return s

        hass.states.get = MagicMock(side_effect=mock_get)

        result_mode, result_pf = coordinator._observe_device_action(
            {
                "thermostats": ["climate.trv1"],
                "acs": ["climate.ac1"],
                "devices": [
                    {"entity_id": "climate.trv1", "type": "trv", "role": "auto", "heating_system_type": ""},
                    {"entity_id": "climate.ac1", "type": "ac", "role": "auto", "heating_system_type": ""},
                ],
            }
        )
        assert result_mode is None
        assert result_pf == 0.0

    @pytest.mark.asyncio
    async def test_learn_only_infer_fallback(self, hass, mock_config_entry):
        """Climate off, no hvac_action -> training uses inferred mode, not skipped (#69)."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}

        # Device in heat mode, at setpoint, no hvac_action -> inferred idle
        device_state = MagicMock()
        device_state.state = "heat"
        device_state.attributes = {
            "current_temperature": 21.0,
            "temperature": 21.0,
            "hvac_modes": ["off", "heat"],
        }
        base_mock = make_mock_states_get(temp="21.0", humidity="55.0")

        def custom_get(eid):
            if eid == "climate.living_room":
                return device_state
            return base_mock(eid)

        hass.states.get = MagicMock(side_effect=custom_get)
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        for _ in range(13):
            await coordinator._async_update_data()

        n_idle, n_heating, n_cooling = coordinator._model_manager.get_mode_counts(
            "living_room_abc12345",
        )
        assert n_idle > 0, "Learn-only mode should use inferred idle when hvac_action is missing"


class TestPerRoomClimateControlDisabled:
    """Tests for per-room climate_control_enabled=False."""

    @pytest.mark.asyncio
    async def test_per_room_disabled_no_service_calls(self, hass, mock_config_entry):
        room = {**SAMPLE_ROOM, "climate_control_enabled": False}
        store = _make_store_mock({"living_room_abc12345": room})
        store.get_settings.return_value = {"climate_control_active": True, "outdoor_temp_sensor": "sensor.outdoor_temp"}
        hass.data = {"roommind": {"store": store}}
        hass.states.get = MagicMock(side_effect=make_mock_states_get(temp="18.0", humidity="55.0"))
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        climate_calls = [c for c in hass.services.async_call.call_args_list if c[0][0] == "climate"]
        assert climate_calls == []

    @pytest.mark.asyncio
    async def test_per_room_disabled_mode_idle(self, hass, mock_config_entry):
        room = {**SAMPLE_ROOM, "climate_control_enabled": False}
        store = _make_store_mock({"living_room_abc12345": room})
        store.get_settings.return_value = {"climate_control_active": True, "outdoor_temp_sensor": "sensor.outdoor_temp"}
        hass.data = {"roommind": {"store": store}}
        hass.states.get = MagicMock(side_effect=make_mock_states_get(temp="18.0", humidity="55.0"))
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room_abc12345"]["mode"] == "idle"

    @pytest.mark.asyncio
    async def test_per_room_disabled_other_room_still_active(self, hass, mock_config_entry):
        disabled_room = {**SAMPLE_ROOM, "climate_control_enabled": False}
        enabled_room = {
            **SAMPLE_ROOM,
            "area_id": "bedroom_xyz",
            "thermostats": ["climate.bedroom"],
            "devices": [{"entity_id": "climate.bedroom", "type": "trv", "role": "auto", "heating_system_type": ""}],
            "temperature_sensor": "sensor.bedroom_temp",
            "humidity_sensor": "sensor.bedroom_humidity",
            "schedules": [{"entity_id": "schedule.bedroom_heating"}],
        }
        store = _make_store_mock(
            {
                "living_room_abc12345": disabled_room,
                "bedroom_xyz": enabled_room,
            }
        )
        store.get_settings.return_value = {"climate_control_active": True, "outdoor_temp_sensor": "sensor.outdoor_temp"}
        hass.data = {"roommind": {"store": store}}
        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                temp="18.0",
                humidity="55.0",
                extra={
                    "sensor.bedroom_temp": ("18.0", {}),
                    "sensor.bedroom_humidity": ("55.0", {}),
                    "schedule.bedroom_heating": ("on", {}),
                    "climate.bedroom": (
                        "heat",
                        {"hvac_modes": ["off", "heat"], "max_temp": 30, "min_temp": 5, "target_temp_step": 0.5},
                    ),
                },
            )
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room_abc12345"]["mode"] == "idle"
        assert data["rooms"]["bedroom_xyz"]["mode"] == "heating"

    @pytest.mark.asyncio
    async def test_per_room_disabled_combined_with_global_disabled(self, hass, mock_config_entry):
        room = {**SAMPLE_ROOM, "climate_control_enabled": False}
        store = _make_store_mock({"living_room_abc12345": room})
        store.get_settings.return_value = {
            "climate_control_active": False,
            "outdoor_temp_sensor": "sensor.outdoor_temp",
        }
        hass.data = {"roommind": {"store": store}}
        hass.states.get = MagicMock(side_effect=make_mock_states_get(temp="18.0", humidity="55.0"))
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room_abc12345"]["mode"] == "idle"
        climate_calls = [c for c in hass.services.async_call.call_args_list if c[0][0] == "climate"]
        assert climate_calls == []

    @pytest.mark.asyncio
    async def test_per_room_disabled_default_true(self, hass, mock_config_entry):
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {"climate_control_active": True, "outdoor_temp_sensor": "sensor.outdoor_temp"}
        hass.data = {"roommind": {"store": store}}
        hass.states.get = MagicMock(side_effect=make_mock_states_get(temp="18.0", humidity="55.0"))
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room_abc12345"]["mode"] == "heating"
