"""A direct-setpoint AC ends its own compressor run, RoomMind must not cut it at the target.

Seen in the simulation (#419 follow-up): the unit holds the room target in its own
deadband, so its compressor only starts ~0.5 K above it. The cooling min run counted
from RoomMind's start ran out while the unit still idled, and RoomMind switched the
unit off the moment the room read the target, a few minutes into the compressor run.
Starts doubled against a unit left to its own thermostat.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from custom_components.roommind.const import MIN_POWER_FRACTION, TargetTemps
from custom_components.roommind.control.mpc_controller import (
    MODE_COOLING,
    MODE_HEATING,
    MODE_IDLE,
    MPCController,
)
from custom_components.roommind.control.mpc_optimizer import MPCPlan
from custom_components.roommind.control.thermal_model import RoomModelManager

from .conftest import build_hass, make_room

AFTER_MIN_RUN = 30 * 60


def _room(climate_mode: str, acs: list[str], *, direct: set[str], thermostats: list[str] | None = None) -> dict:
    room = make_room(climate_mode=climate_mode, acs=acs, thermostats=thermostats or [])
    for dev in room["devices"]:
        if dev["entity_id"] in direct:
            dev["setpoint_mode"] = "direct"
    return room


def _state(hvac_action: str | None, state: str = "cool") -> MagicMock:
    s = MagicMock()
    s.state = state
    s.attributes = {"hvac_modes": ["off", "heat", "cool"]}
    if hvac_action is not None:
        s.attributes["hvac_action"] = hvac_action
    return s


def _controller(room: dict, mode: str, states: dict, outdoor_temp: float = 30.0) -> MPCController:
    hass = build_hass()
    hass.states.get = MagicMock(side_effect=states.get)
    return MPCController(
        hass,
        room,
        model_manager=RoomModelManager(),
        outdoor_temp=outdoor_temp,
        settings={},
        has_external_sensor=True,
        previous_mode=mode,
        mode_on_since=time.time() - AFTER_MIN_RUN,
    )


@pytest.fixture(autouse=True)
def _idle_plan(monkeypatch):
    plan = MPCPlan(actions=[MODE_IDLE] * 24, temperatures=[24.0] * 25, power_fractions=[0.0] * 24)
    monkeypatch.setattr(
        "custom_components.roommind.control.mpc_controller.MPCOptimizer.optimize",
        lambda *a, **kw: plan,
    )


COOL_ROOM = _room("cool_only", ["climate.ac1"], direct={"climate.ac1"})


@pytest.mark.parametrize("heat", [None, 24.0], ids=["no_heat_side", "cool_only_mirrors_the_target"])
def test_a_direct_unit_still_cooling_keeps_its_run_at_the_target(heat):
    ctrl = _controller(COOL_ROOM, MODE_COOLING, {"climate.ac1": _state("cooling")})
    mode, pf = ctrl._evaluate_mpc(24.0, TargetTemps(heat=heat, cool=24.0))
    assert mode == MODE_COOLING
    assert pf >= MIN_POWER_FRACTION


def test_a_direct_unit_that_stopped_by_itself_is_switched_off():
    ctrl = _controller(COOL_ROOM, MODE_COOLING, {"climate.ac1": _state("idle")})
    assert ctrl._evaluate_mpc(23.6, TargetTemps(heat=None, cool=24.0)) == (MODE_IDLE, 0.0)


@pytest.mark.parametrize(
    "state", [None, _state(None), _state("cooling", state="unavailable"), _state("cooling", state="off")]
)
def test_a_unit_without_a_usable_action_keeps_the_plain_min_run(state):
    ctrl = _controller(COOL_ROOM, MODE_COOLING, {"climate.ac1": state} if state else {})
    assert ctrl._evaluate_mpc(24.0, TargetTemps(heat=None, cool=24.0)) == (MODE_IDLE, 0.0)


def test_a_proportional_unit_keeps_the_plain_min_run():
    room = _room("cool_only", ["climate.ac1"], direct=set())
    ctrl = _controller(room, MODE_COOLING, {"climate.ac1": _state("cooling")})
    assert ctrl._evaluate_mpc(24.0, TargetTemps(heat=None, cool=24.0)) == (MODE_IDLE, 0.0)


def test_a_proportional_unit_next_to_it_is_not_kept_boosting():
    room = _room("cool_only", ["climate.ac1", "climate.ac2"], direct={"climate.ac1"})
    states = {"climate.ac1": _state("cooling"), "climate.ac2": _state("idle")}
    ctrl = _controller(room, MODE_COOLING, states)
    assert ctrl._evaluate_mpc(24.0, TargetTemps(heat=None, cool=24.0)) == (MODE_IDLE, 0.0)


def test_one_of_two_direct_units_still_running_keeps_the_room_cooling():
    room = _room("cool_only", ["climate.ac1", "climate.ac2"], direct={"climate.ac1", "climate.ac2"})
    states = {"climate.ac1": _state("idle"), "climate.ac2": _state("cooling")}
    ctrl = _controller(room, MODE_COOLING, states)
    assert ctrl._evaluate_mpc(24.0, TargetTemps(heat=None, cool=24.0))[0] == MODE_COOLING


@pytest.mark.parametrize(("temp", "expected"), [(23.6, MODE_COOLING), (23.5, MODE_IDLE), (22.9, MODE_IDLE)])
def test_the_run_ends_half_a_degree_past_the_target(temp, expected):
    """A unit reading warm (or reporting its mode as action) would cool on to the hard ceiling."""
    ctrl = _controller(COOL_ROOM, MODE_COOLING, {"climate.ac1": _state("cooling")})
    assert ctrl._evaluate_mpc(temp, TargetTemps(heat=None, cool=24.0))[0] == expected


def test_cooling_never_continues_into_the_heat_target():
    """Narrow auto band: running on past the heat target would trade a cooling run for a heating one."""
    room = _room("auto", ["climate.ac1"], direct={"climate.ac1"})
    ctrl = _controller(room, MODE_COOLING, {"climate.ac1": _state("cooling")})
    assert ctrl._evaluate_mpc(23.5, TargetTemps(heat=23.5, cool=24.0)) == (MODE_IDLE, 0.0)
    assert ctrl._evaluate_mpc(23.6, TargetTemps(heat=23.5, cool=24.0))[0] == MODE_COOLING


@pytest.mark.parametrize("action", ["heating", "preheating", "defrosting"])
def test_a_direct_unit_still_heating_keeps_its_run_at_the_target(action):
    room = _room("heat_only", ["climate.ac1"], direct={"climate.ac1"})
    ctrl = _controller(room, MODE_HEATING, {"climate.ac1": _state(action, state="heat")}, outdoor_temp=5.0)
    assert ctrl._evaluate_mpc(21.0, TargetTemps(heat=21.0, cool=None))[0] == MODE_HEATING
    assert ctrl._evaluate_mpc(21.5, TargetTemps(heat=21.0, cool=None)) == (MODE_IDLE, 0.0)


@pytest.mark.parametrize(
    ("mode", "targets"),
    [(MODE_HEATING, TargetTemps(heat=None, cool=24.0)), (MODE_COOLING, TargetTemps(heat=21.0, cool=None))],
)
def test_no_target_on_the_running_side_ends_the_run(mode, targets):
    room = _room("auto", ["climate.ac1"], direct={"climate.ac1"})
    action = "heating" if mode == MODE_HEATING else "cooling"
    ctrl = _controller(room, mode, {"climate.ac1": _state(action)})
    assert ctrl._direct_unit_running(mode, 22.0, targets) is False


def test_heating_never_continues_into_the_cool_target():
    room = _room("auto", ["climate.ac1"], direct={"climate.ac1"})
    ctrl = _controller(room, MODE_HEATING, {"climate.ac1": _state("heating", state="heat")}, outdoor_temp=5.0)
    assert ctrl._evaluate_mpc(21.5, TargetTemps(heat=21.0, cool=21.5)) == (MODE_IDLE, 0.0)


def test_heating_with_trvs_in_the_room_keeps_the_plain_min_run():
    """A TRV heats along on its proportional boost, keeping the run on would overheat."""
    room = _room("heat_only", ["climate.ac1"], direct={"climate.ac1"}, thermostats=["climate.trv1"])
    room["heat_source_orchestration"] = True
    states = {"climate.ac1": _state("heating", state="heat"), "climate.trv1": _state("idle", state="heat")}
    ctrl = _controller(room, MODE_HEATING, states, outdoor_temp=5.0)
    assert ctrl._evaluate_mpc(21.0, TargetTemps(heat=21.0, cool=None)) == (MODE_IDLE, 0.0)


@pytest.mark.parametrize(
    ("action", "temp", "expected"),
    [("cooling", 23.6, MODE_COOLING), ("idle", 23.6, MODE_IDLE), ("cooling", 23.5, MODE_IDLE)],
)
def test_bangbang_keeps_a_direct_units_own_cooling_run(action, temp, expected):
    ctrl = _controller(COOL_ROOM, MODE_COOLING, {"climate.ac1": _state(action)})
    assert ctrl._evaluate_bangbang(temp, TargetTemps(heat=24.0, cool=24.0)) == expected


def test_simple_mode_ends_a_direct_units_run_half_a_degree_past_the_target():
    simple = _controller(COOL_ROOM, MODE_COOLING, {"climate.ac1": _state("cooling")})
    simple._mpc_enabled = False
    assert simple._evaluate_bangbang(23.5, TargetTemps(heat=None, cool=24.0)) == MODE_IDLE


@pytest.mark.parametrize(("action", "expected"), [("heating", MODE_HEATING), ("idle", MODE_IDLE)])
def test_bangbang_keeps_a_direct_units_own_heating_run(action, expected):
    room = _room("heat_only", ["climate.ac1"], direct={"climate.ac1"})
    ctrl = _controller(room, MODE_HEATING, {"climate.ac1": _state(action, state="heat")}, outdoor_temp=5.0)
    assert ctrl._evaluate_bangbang(21.4, TargetTemps(heat=21.0, cool=None)) == expected
