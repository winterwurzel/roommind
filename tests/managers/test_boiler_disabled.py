"""Tests for heating with the boiler switched off globally.

The point of the switch is that the boiler only runs when a room is genuinely
cold, and electric sources only run on surplus. These tests pin that: electric
sources need the prefer-electric flag, TRVs only heat below the floor and only
up to it, and boiler-only rooms are covered too.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.roommind.const import MODE_HEATING
from custom_components.roommind.managers.heat_source_orchestrator import (
    evaluate_heat_sources,
)

TRV = "climate.trv"
AC = "climate.ac"
HEATER = "climate.heater"
FLOOR = 17.0


def _hass(unavailable=()):
    """Minimal hass: every entity available unless listed, ACs report heat modes."""
    hass = MagicMock()

    def _get(entity_id):
        state = MagicMock()
        state.state = "unavailable" if entity_id in unavailable else "heat"
        state.attributes = {"hvac_modes": ["off", "heat", "cool"]} if entity_id == AC else {}
        return state

    hass.states.get = _get
    return hass


def _room(*device_types, prefer_electric=False, orchestration=True):
    eids = {"trv": TRV, "ac": AC, "electric": HEATER}
    return {
        "area_id": "room",
        "heat_source_orchestration": orchestration,
        "heat_source_ac_min_outdoor": -15.0,
        "prefer_electric_heat": prefer_electric,
        "devices": [{"entity_id": eids[t], "type": t, "role": "auto"} for t in device_types],
    }


def _plan(room, current_temp=20.0, target_temp=21.0, outdoor_temp=5.0, previous="none", hass=None, floor=FLOOR):
    return evaluate_heat_sources(
        room_config=room,
        mode=MODE_HEATING,
        power_fraction=0.8,
        current_temp=current_temp,
        target_temp=target_temp,
        outdoor_temp=outdoor_temp,
        previous_active_sources=previous,
        hass=hass or _hass(),
        boiler_enabled=False,
        boiler_floor_temp=floor,
    )


def _cmd(plan, entity_id):
    return next(c for c in plan.commands if c.entity_id == entity_id)


def test_no_surplus_nothing_heats():
    """Above the floor and without the flag, neither the boiler nor the AC runs."""
    plan = _plan(_room("trv", "ac"))
    assert plan.active_sources == "none"
    assert _cmd(plan, TRV).active is False
    assert _cmd(plan, AC).active is False


def test_surplus_runs_ac_not_boiler():
    """With the flag the AC heats and the TRV stays off, even far below target."""
    plan = _plan(_room("trv", "ac", prefer_electric=True), current_temp=18.0, target_temp=22.0)
    assert plan.active_sources == "secondary"
    assert _cmd(plan, AC).active is True
    assert _cmd(plan, TRV).active is False


def test_surplus_runs_electric_heater_in_room_without_ac():
    """Resistive heaters take part too - bathroom/WC have no AC."""
    plan = _plan(_room("trv", "electric", prefer_electric=True))
    assert plan.active_sources == "secondary"
    assert _cmd(plan, HEATER).active is True
    assert _cmd(plan, HEATER).device_type == "electric"


def test_below_floor_boiler_heats_to_floor_only():
    """The fallback runs the TRV, aimed at the floor rather than the comfort target."""
    plan = _plan(_room("trv", "ac"), current_temp=16.5, target_temp=21.0)
    assert plan.active_sources == "primary"
    trv = _cmd(plan, TRV)
    assert trv.active is True
    assert trv.target_temp == FLOOR
    assert _cmd(plan, AC).active is False


def test_fallback_holds_through_hysteresis_then_stops():
    """Once running, the fallback holds just above the floor, then releases."""
    room = _room("trv")
    assert _plan(room, current_temp=17.1, previous="primary").active_sources == "primary"
    assert _plan(room, current_temp=17.1, previous="none").active_sources == "none"
    assert _plan(room, current_temp=17.4, previous="primary").active_sources == "none"


def test_floor_above_target_is_capped_at_target():
    """A room whose target sits below the floor must not be heated past its target."""
    plan = _plan(_room("trv"), current_temp=15.0, target_temp=16.0, floor=17.0)
    assert _cmd(plan, TRV).target_temp == 16.0


def test_boiler_only_room_is_covered_without_orchestration_flag():
    """Rooms with just TRVs get a plan too, so their TRVs are idled explicitly."""
    plan = _plan(_room("trv", orchestration=False))
    assert plan is not None
    assert plan.active_sources == "none"
    assert _cmd(plan, TRV).active is False


def test_surplus_and_cold_run_both():
    """A cold room on surplus uses both: electric to target, boiler to the floor."""
    plan = _plan(_room("trv", "ac", prefer_electric=True), current_temp=16.0)
    assert plan.active_sources == "both"
    assert _cmd(plan, TRV).target_temp == FLOOR
    assert _cmd(plan, AC).target_temp is None


def test_at_target_nothing_heats_even_below_floor_setting():
    """No gap means no heat, whatever the flags say."""
    plan = _plan(_room("trv", "ac", prefer_electric=True), current_temp=21.0, target_temp=21.0)
    assert plan.active_sources == "none"


def test_unavailable_heater_is_not_selected():
    """An unavailable electric heater is skipped, leaving the room idle."""
    plan = _plan(_room("trv", "electric", prefer_electric=True), hass=_hass(unavailable={HEATER}))
    assert plan.active_sources == "none"
    assert _cmd(plan, HEATER).active is False


def test_ac_skipped_in_extreme_cold():
    """The AC's cold-weather cutoff still applies with the boiler disabled."""
    plan = _plan(_room("trv", "ac", prefer_electric=True), outdoor_temp=-20.0)
    assert _cmd(plan, AC).active is False


def test_missing_temperature_idles_everything():
    """Without a reading nothing may heat - the safe default with the boiler off."""
    plan = _plan(_room("trv", "ac", prefer_electric=True), current_temp=None)
    assert plan.active_sources == "none"
    assert not any(c.active for c in plan.commands)


def test_room_without_trvs_is_left_to_normal_control():
    """The boiler cannot affect an AC-only room, so no plan is produced."""
    assert _plan(_room("ac", prefer_electric=True)) is None
