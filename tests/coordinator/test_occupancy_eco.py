"""Tests for the occupancy eco fallback: an empty room drops to eco (#180)."""

from __future__ import annotations

import time
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import State
from homeassistant.util import dt as dt_util

from custom_components.roommind.utils import schedule_utils

from .conftest import (
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    make_mock_states_get,
)

AREA = "living_room_abc12345"
MOTION = "binary_sensor.living_room_motion"
OCCUPANCY_ROOM = {
    **SAMPLE_ROOM,
    "occupancy_sensors": [MOTION],
    "occupancy_eco_enabled": True,
    "occupancy_eco_delay": 5,
}


async def _run(hass, mock_config_entry, room, motion_state="off", settings=None, changed_ago_s=3600.0, **states):
    store = _make_store_mock({AREA: room}, settings)
    hass.data = {"roommind": {"store": store}}
    base = make_mock_states_get(**states)
    motion = State(MOTION, motion_state, last_changed=dt_util.utcnow() - timedelta(seconds=changed_ago_s))
    hass.states.get = lambda eid: motion if eid == MOTION else base(eid)
    hass.services.async_call = AsyncMock()
    coordinator = _create_coordinator(hass, mock_config_entry)
    data = await coordinator._async_update_data()
    return coordinator, data["rooms"][AREA]


@pytest.mark.asyncio
async def test_empty_room_uses_eco(hass, mock_config_entry):
    _, room = await _run(hass, mock_config_entry, OCCUPANCY_ROOM)
    assert room["target_temp"] == 17.0
    assert room["occupancy_vacant"] is True


@pytest.mark.asyncio
async def test_occupied_room_follows_schedule(hass, mock_config_entry):
    _, room = await _run(hass, mock_config_entry, OCCUPANCY_ROOM, motion_state="on")
    assert room["target_temp"] == 21.0
    assert room["occupancy_vacant"] is False


@pytest.mark.asyncio
async def test_disabled_by_default(hass, mock_config_entry):
    room_cfg = {k: v for k, v in OCCUPANCY_ROOM.items() if k not in ("occupancy_eco_enabled", "occupancy_eco_delay")}
    _, room = await _run(hass, mock_config_entry, room_cfg)
    assert room["target_temp"] == 21.0
    assert room["occupancy_vacant"] is False


@pytest.mark.asyncio
async def test_delay_keeps_comfort_until_it_runs_out(hass, mock_config_entry):
    room_cfg = {**OCCUPANCY_ROOM, "occupancy_eco_delay": 15}
    _, room = await _run(hass, mock_config_entry, room_cfg, changed_ago_s=14 * 60)
    assert room["target_temp"] == 21.0
    assert room["occupancy_vacant"] is False


@pytest.mark.asyncio
async def test_override_beats_empty_room(hass, mock_config_entry):
    room_cfg = {
        **OCCUPANCY_ROOM,
        "override_heat": 23.0,
        "override_cool": 23.0,
        "override_until": time.time() + 3600,
        "override_type": "custom",
    }
    _, room = await _run(hass, mock_config_entry, room_cfg)
    assert room["target_temp"] == 23.0


@pytest.mark.asyncio
async def test_schedule_off_action_off_is_not_raised_to_eco(hass, mock_config_entry):
    _, room = await _run(
        hass,
        mock_config_entry,
        OCCUPANCY_ROOM,
        settings={"schedule_off_action": "off"},
        schedule_state="off",
    )
    assert room["force_off"] is True


@pytest.mark.asyncio
async def test_mpc_horizon_sees_the_empty_room(hass, mock_config_entry):
    """The resolver for the MPC horizon gets the vacancy too, else it pre-heats an empty room."""
    with patch.object(schedule_utils, "make_target_resolver", wraps=schedule_utils.make_target_resolver) as resolver:
        await _run(hass, mock_config_entry, OCCUPANCY_ROOM)
    assert resolver.call_args.kwargs["room_vacant"] is True
