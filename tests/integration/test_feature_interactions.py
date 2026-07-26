"""Integration: cross-feature interactions that might produce unexpected side effects."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from .conftest import ROOM_LIVING, make_hass_states, setup_room


class TestVacationAndOverride:
    @pytest.mark.asyncio
    async def test_override_beats_vacation(self, coordinator, real_store):
        """Active override should take priority over vacation mode."""
        await setup_room(
            real_store,
            settings={
                "outdoor_temp_sensor": "sensor.outdoor_temp",
                "vacation_temp": 15.0,
                "vacation_until": time.time() + 86400,
            },
        )
        await real_store.async_update_room(
            "living_room",
            {
                "override_heat": 25.0,
                "override_cool": 25.0,
                "override_until": time.time() + 3600,
                "override_type": "custom",
            },
        )

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["target_temp"] == 25.0

    @pytest.mark.asyncio
    async def test_vacation_beats_schedule(self, coordinator, real_store):
        """Vacation mode should override schedule-based comfort temp."""
        await setup_room(
            real_store,
            settings={
                "outdoor_temp_sensor": "sensor.outdoor_temp",
                "vacation_temp": 15.0,
                "vacation_until": time.time() + 86400,
            },
        )

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["target_temp"] == 15.0

    @pytest.mark.asyncio
    async def test_expired_vacation_falls_back(self, coordinator, real_store):
        """Expired vacation should revert to normal schedule."""
        await setup_room(
            real_store,
            settings={
                "outdoor_temp_sensor": "sensor.outdoor_temp",
                "vacation_temp": 15.0,
                "vacation_until": time.time() - 1,
            },
        )

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["target_temp"] == 21.0


class TestPresenceInteractions:
    @pytest.mark.asyncio
    async def test_presence_away_uses_eco(self, coordinator, real_store):
        """When all persons are away, room should use eco temp even if schedule is on."""
        await setup_room(
            real_store,
            settings={
                "outdoor_temp_sensor": "sensor.outdoor_temp",
                "presence_enabled": True,
                "presence_persons": ["person.kevin"],
            },
        )
        coordinator.hass.states.get = MagicMock(
            side_effect=make_hass_states(
                extra={"person.kevin": "not_home"},
            )
        )

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["presence_away"] is True
        assert data["rooms"]["living_room"]["target_temp"] == 17.0

    @pytest.mark.asyncio
    async def test_presence_home_uses_comfort(self, coordinator, real_store):
        """When person is home and schedule is on, comfort temp should be used."""
        await setup_room(
            real_store,
            settings={
                "outdoor_temp_sensor": "sensor.outdoor_temp",
                "presence_enabled": True,
                "presence_persons": ["person.kevin"],
            },
        )
        coordinator.hass.states.get = MagicMock(
            side_effect=make_hass_states(
                extra={"person.kevin": "home"},
            )
        )

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["presence_away"] is False
        assert data["rooms"]["living_room"]["target_temp"] == 21.0

    @pytest.mark.asyncio
    async def test_override_beats_presence_away(self, coordinator, real_store):
        """Override should take priority even when presence is away."""
        await setup_room(
            real_store,
            settings={
                "outdoor_temp_sensor": "sensor.outdoor_temp",
                "presence_enabled": True,
                "presence_persons": ["person.kevin"],
            },
        )
        await real_store.async_update_room(
            "living_room",
            {
                "override_heat": 23.0,
                "override_cool": 23.0,
                "override_until": time.time() + 3600,
                "override_type": "custom",
            },
        )
        coordinator.hass.states.get = MagicMock(
            side_effect=make_hass_states(
                extra={"person.kevin": "not_home"},
            )
        )

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["target_temp"] == 23.0

    @pytest.mark.asyncio
    async def test_per_room_presence_overrides_global(self, coordinator, real_store):
        """Per-room presence persons should override global list."""
        room = {**ROOM_LIVING, "presence_persons": ["person.anna"]}
        await setup_room(
            real_store,
            room,
            settings={
                "outdoor_temp_sensor": "sensor.outdoor_temp",
                "presence_enabled": True,
                "presence_persons": ["person.kevin"],
            },
        )
        coordinator.hass.states.get = MagicMock(
            side_effect=make_hass_states(
                extra={
                    "person.kevin": "not_home",
                    "person.anna": "home",
                },
            )
        )

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["presence_away"] is False
        assert data["rooms"]["living_room"]["target_temp"] == 21.0


class TestWindowAndOverride:
    @pytest.mark.asyncio
    async def test_window_open_idles_even_with_override(self, coordinator, real_store):
        """Window open should force idle even with active override."""
        room = {**ROOM_LIVING, "window_sensors": ["binary_sensor.window_living"]}
        await setup_room(real_store, room)
        await real_store.async_update_room(
            "living_room",
            {
                "override_heat": 25.0,
                "override_cool": 25.0,
                "override_until": time.time() + 3600,
                "override_type": "boost",
            },
        )
        coordinator.hass.states.get = MagicMock(
            side_effect=make_hass_states(
                extra={"binary_sensor.window_living": "on"},
            )
        )

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["window_open"] is True
        assert data["rooms"]["living_room"]["mode"] == "idle"
        # Target temp should still reflect the override (just not acting on it)
        assert data["rooms"]["living_room"]["target_temp"] == 25.0


class TestClimateModeBoundaries:
    @pytest.mark.asyncio
    async def test_heat_only_never_cools(self, coordinator, real_store):
        """In heat_only mode, room should never cool even if temp is above target."""
        room = {**ROOM_LIVING, "climate_mode": "heat_only", "comfort_temp": 18.0}
        await setup_room(real_store, room)
        coordinator.hass.states.get = MagicMock(side_effect=make_hass_states(temp="22.0"))

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["mode"] != "cooling"

    @pytest.mark.asyncio
    async def test_outdoor_gating_prevents_heating_in_mpc(self, coordinator, real_store):
        """With outdoor temp above heating max, heating should be gated in MPC."""
        await setup_room(
            real_store,
            settings={
                "outdoor_temp_sensor": "sensor.outdoor_temp",
                "outdoor_heating_max": 22.0,
            },
        )
        # Outdoor is 25C (above max), indoor is 18C (below target)
        coordinator.hass.states.get = MagicMock(side_effect=make_hass_states(temp="18.0", outdoor_temp="25.0"))

        data = await coordinator._async_update_data()

        # Outdoor gating blocks heating via get_can_heat_cool in MPC controller
        # In on/off fallback mode (no model trained), gating still applies
        assert data["rooms"]["living_room"]["mode"] == "idle"


class TestRoomRemovalCleanup:
    @pytest.mark.asyncio
    async def test_removed_room_disappears_from_output(self, coordinator, real_store):
        """Removing a room should exclude it from coordinator output."""
        await setup_room(real_store)

        await coordinator._async_update_data()
        assert "living_room" in coordinator._previous_modes

        await real_store.async_delete_room("living_room")
        data = await coordinator._async_update_data()

        assert "living_room" not in data["rooms"]

    @pytest.mark.asyncio
    async def test_sensor_goes_unavailable_mid_session(self, coordinator, real_store):
        """Sensor becoming unavailable should not crash the coordinator."""
        await setup_room(real_store)

        await coordinator._async_update_data()

        coordinator.hass.states.get = MagicMock(side_effect=make_hass_states(temp="unavailable"))
        data = await coordinator._async_update_data()

        assert "living_room" in data["rooms"]
