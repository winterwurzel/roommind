"""After a reload or restart a run inside the start hysteresis band must not be stopped once.

The previous mode lives in memory only. Before, a TRV heating a room at 20.9 (target 21)
read as "idle" after the restart, the start hysteresis (start below 20.8) kept it idle and
the device was switched off for one cycle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.const import MODE_COOLING, MODE_HEATING, MODE_IDLE
from custom_components.roommind.control import mpc_controller
from custom_components.roommind.control.mpc_controller import infer_running_mode

from .conftest import SAMPLE_ROOM, _create_coordinator, _make_store_mock, make_mock_states_get

AREA = "living_room_abc12345"
TRV = "climate.living_room"


def _state(state: str, action: str | None):
    s = MagicMock()
    s.state = state
    s.attributes = {"hvac_action": action, "hvac_modes": ["off", "heat"], "temperature": 28.0}
    return s


def _room(*eids_types):
    return {"devices": [{"entity_id": e, "type": t} for e, t in eids_types]}


@pytest.fixture(autouse=True)
def _clean_cache():
    mpc_controller.clear_command_cache()
    yield
    mpc_controller.clear_command_cache()


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ({TRV: _state("heat", "heating")}, MODE_HEATING),
        ({TRV: _state("heat", "preheating")}, MODE_HEATING),
        ({TRV: _state("heat", "idle")}, MODE_IDLE),
        ({TRV: _state("off", "off")}, MODE_IDLE),
        ({TRV: None}, MODE_IDLE),
        ({TRV: _state("unavailable", None)}, None),
        ({TRV: _state("cool", "cooling")}, MODE_COOLING),
        ({TRV: _state("heat", "heating"), "climate.ac": _state("cool", "cooling")}, MODE_IDLE),
    ],
)
def test_infer_running_mode(states, expected):
    hass = MagicMock()
    hass.states.get = MagicMock(side_effect=states.get)
    room = _room(*((eid, "trv" if eid == TRV else "ac") for eid in states))
    assert infer_running_mode(hass, room) == expected


def test_a_sitting_out_ac_does_not_seed_a_heating_run():
    """Heat only with a TRV: an AC an older release left heating must not start a TRV run (#476)."""
    hass = MagicMock()
    states = {TRV: _state("heat", "idle"), "climate.ac": _state("heat", "heating")}
    hass.states.get = MagicMock(side_effect=states.get)
    room = {**_room((TRV, "trv"), ("climate.ac", "ac")), "climate_mode": "heat_only"}

    assert infer_running_mode(hass, room) == MODE_IDLE
    assert infer_running_mode(hass, {**room, "climate_mode": "auto"}) == MODE_HEATING


def test_a_device_roommind_switched_off_is_a_manual_run():
    """Across an integration reload the command cache survives: RoomMind's last word was off."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_state("heat", "heating"))
    mpc_controller._last_commands[TRV] = {"service": "set_hvac_mode", "hvac_mode": "off"}
    assert infer_running_mode(hass, _room((TRV, "trv"))) == MODE_IDLE


async def _first_cycle(hass, mock_config_entry, trv_state):
    store = _make_store_mock({AREA: {**SAMPLE_ROOM, "climate_mode": "heat_only"}})
    hass.data = {"roommind": {"store": store}}
    base = make_mock_states_get(temp="20.9")
    hass.states.get = MagicMock(side_effect=lambda eid: trv_state if eid == TRV else base(eid))
    hass.services.async_call = AsyncMock()
    coordinator = _create_coordinator(hass, mock_config_entry)
    data = await coordinator._async_update_data()
    return coordinator, data["rooms"][AREA]


def _turned_off(hass) -> bool:
    return any(
        c[0][1] == "set_hvac_mode" and c[0][2].get("hvac_mode") == "off"
        for c in hass.services.async_call.call_args_list
    )


@pytest.mark.asyncio
async def test_running_trv_inside_the_band_keeps_heating_after_a_restart(hass, mock_config_entry):
    coordinator, room = await _first_cycle(hass, mock_config_entry, _state("heat", "heating"))
    assert room["mode"] == MODE_HEATING
    assert not _turned_off(hass)
    # the adopted run counts as started now (its real start is unknown)
    assert AREA in coordinator._mode_on_since


@pytest.mark.asyncio
async def test_idle_trv_inside_the_band_stays_idle_after_a_restart(hass, mock_config_entry):
    _, room = await _first_cycle(hass, mock_config_entry, _state("heat", "idle"))
    assert room["mode"] == MODE_IDLE


@pytest.mark.asyncio
async def test_seed_only_until_the_first_controlled_cycle(hass, mock_config_entry):
    """Once RoomMind controlled the room, a heating device no longer seeds anything: an
    idle decision there is RoomMind's own and must stick."""
    coordinator, _ = await _first_cycle(hass, mock_config_entry, _state("heat", "idle"))
    assert AREA in coordinator._mode_seeded
    hass.states.get = MagicMock(
        side_effect=lambda eid: _state("heat", "heating") if eid == TRV else make_mock_states_get(temp="20.9")(eid)
    )
    data = await coordinator._async_update_data()
    assert data["rooms"][AREA]["mode"] == MODE_IDLE


@pytest.mark.asyncio
async def test_seed_waits_for_a_device_that_is_unavailable_after_the_restart(hass, mock_config_entry):
    """Zigbee TRVs report unavailable for a while after an HA restart: the seed must not be
    used up by that cycle, or the run is switched off once the device is back."""
    coordinator, room = await _first_cycle(hass, mock_config_entry, _state("unavailable", None))
    assert room["mode"] == MODE_IDLE
    assert AREA not in coordinator._mode_seeded
    hass.states.get = MagicMock(
        side_effect=lambda eid: _state("heat", "heating") if eid == TRV else make_mock_states_get(temp="20.9")(eid)
    )
    hass.services.async_call.reset_mock()
    data = await coordinator._async_update_data()
    assert data["rooms"][AREA]["mode"] == MODE_HEATING
    assert not _turned_off(hass)
    assert AREA in coordinator._mode_seeded


@pytest.mark.asyncio
async def test_disabled_room_does_not_seed_every_cycle(hass, mock_config_entry, monkeypatch):
    """Learn-only rooms (climate control off) never seed a run from the devices."""
    calls: list[str] = []
    monkeypatch.setattr(
        "custom_components.roommind.coordinator.infer_running_mode",
        lambda *a, **k: calls.append("seed") or MODE_HEATING,
    )
    store = _make_store_mock({AREA: {**SAMPLE_ROOM, "climate_mode": "heat_only", "climate_control_enabled": False}})
    hass.data = {"roommind": {"store": store}}
    base = make_mock_states_get(temp="20.9")
    hass.states.get = MagicMock(side_effect=lambda eid: _state("heat", "heating") if eid == TRV else base(eid))
    hass.services.async_call = AsyncMock()
    coordinator = _create_coordinator(hass, mock_config_entry)
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    assert calls == []


def test_rebound_tracker_seeded_run_starts_charged():
    from custom_components.roommind.managers.residual_heat_tracker import AcReboundTracker

    tracker = AcReboundTracker()
    tracker.seed_running("r")
    assert tracker.get_charge("r") == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_seeded_run_drops_an_old_idle_start(hass, mock_config_entry, monkeypatch):
    """A run adopted from the devices is a run: the setback hold must not count the idle before it (#255)."""
    coordinator, room = await _first_cycle(hass, mock_config_entry, _state("unavailable", None))
    assert room["mode"] == MODE_IDLE
    coordinator._idle_since[AREA] -= 7200
    seen: list[float | None] = []
    real = mpc_controller.MPCController

    def spy(*a, **kw):
        seen.append(kw.get("idle_since"))
        return real(*a, **kw)

    monkeypatch.setattr("custom_components.roommind.coordinator.MPCController", spy)
    hass.states.get = MagicMock(
        side_effect=lambda eid: _state("heat", "heating") if eid == TRV else make_mock_states_get(temp="20.9")(eid)
    )
    await coordinator._async_update_data()

    assert seen == [None]
