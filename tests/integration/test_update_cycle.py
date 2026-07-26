"""Integration: full coordinator update cycle with real managers + MPC."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from .conftest import ROOM_LIVING, make_hass_states, setup_room


class TestUpdateCycle:
    @pytest.mark.asyncio
    async def test_heating_cycle_produces_service_call(self, coordinator, real_store):
        await setup_room(real_store)

        data = await coordinator._async_update_data()

        rooms = data["rooms"]
        assert "living_room" in rooms
        room = rooms["living_room"]
        assert room["target_temp"] == 21.0
        assert room["mode"] in ("heating", "idle")
        assert coordinator.hass.services.async_call.called

    @pytest.mark.asyncio
    async def test_eco_temp_when_schedule_off(self, coordinator, real_store):
        await setup_room(real_store)
        coordinator.hass.states.get = MagicMock(side_effect=make_hass_states(schedule_state="off"))

        data = await coordinator._async_update_data()

        assert data["rooms"]["living_room"]["target_temp"] == 17.0

    @pytest.mark.asyncio
    async def test_override_takes_priority(self, coordinator, real_store):
        await setup_room(real_store)
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
    async def test_multiple_rooms_independent(self, coordinator, real_store):
        await setup_room(real_store)

        room2 = {
            **ROOM_LIVING,
            "area_id": "bedroom",
            "thermostats": ["climate.bedroom"],
            "temperature_sensor": "sensor.bedroom_temp",
            "humidity_sensor": "sensor.bedroom_humidity",
            "schedules": [{"entity_id": "schedule.bedroom"}],
            "comfort_temp": 19.0,
            "comfort_heat": 19.0,
            "eco_temp": 16.0,
            "eco_heat": 16.0,
        }
        await real_store.async_save_room("bedroom", room2)

        coordinator.hass.states.get = MagicMock(
            side_effect=make_hass_states(
                extra={
                    "sensor.bedroom_temp": ("17.0", {}),
                    "sensor.bedroom_humidity": ("50.0", {}),
                    "schedule.bedroom": ("on", {}),
                    "climate.bedroom": ("idle", {"hvac_modes": ["off", "heat"], "hvac_action": "idle"}),
                }
            )
        )

        data = await coordinator._async_update_data()

        rooms = data["rooms"]
        assert rooms["living_room"]["target_temp"] == 21.0
        assert rooms["bedroom"]["target_temp"] == 19.0
