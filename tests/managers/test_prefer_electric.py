"""Tests for the per-room prefer-electric heat source override.

The point of the flag is that the boiler runs less when surplus PV is available,
so these tests pin the two behaviours that make that true: the AC wins in cold
weather (where the outdoor threshold would otherwise pick the boiler), and a
large temperature gap does not escalate to running both sources.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.roommind.const import MODE_HEATING
from custom_components.roommind.managers.heat_source_orchestrator import (
    evaluate_heat_sources,
)

TRV = "climate.trv"
AC = "climate.ac"


def _hass(ac_hvac_modes=("off", "heat", "cool")):
    """Minimal hass whose states.get returns available entities."""
    hass = MagicMock()

    def _get(entity_id):
        state = MagicMock()
        state.state = "heat"
        state.attributes = {"hvac_modes": list(ac_hvac_modes)} if entity_id == AC else {}
        return state

    hass.states.get = _get
    return hass


def _room(prefer_electric: bool, **overrides):
    room = {
        "area_id": "living_room",
        "heat_source_orchestration": True,
        "heat_source_primary_delta": 1.5,
        "heat_source_outdoor_threshold": 15.0,
        "heat_source_ac_min_outdoor": -15.0,
        "prefer_electric_heat": prefer_electric,
        "devices": [
            {"entity_id": TRV, "type": "trv", "role": "auto", "heating_system_type": ""},
            {"entity_id": AC, "type": "ac", "role": "auto", "heating_system_type": ""},
        ],
    }
    room.update(overrides)
    return room


def _plan(room, current_temp=20.0, target_temp=21.0, outdoor_temp=5.0, previous="none"):
    return evaluate_heat_sources(
        room_config=room,
        mode=MODE_HEATING,
        power_fraction=0.8,
        current_temp=current_temp,
        target_temp=target_temp,
        outdoor_temp=outdoor_temp,
        previous_active_sources=previous,
        hass=_hass(),
    )


def _active(plan, entity_id):
    return next(c.active for c in plan.commands if c.entity_id == entity_id)


def test_cold_weather_uses_boiler_without_flag():
    """Baseline: below the outdoor threshold the boiler is chosen, AC stays idle."""
    plan = _plan(_room(prefer_electric=False), outdoor_temp=5.0)
    assert plan.active_sources == "primary"
    assert _active(plan, TRV) is True
    assert _active(plan, AC) is False


def test_cold_weather_uses_ac_with_flag():
    """The flag flips that: AC heats, boiler stays off, even well below threshold."""
    plan = _plan(_room(prefer_electric=True), outdoor_temp=5.0)
    assert plan.active_sources == "secondary"
    assert _active(plan, AC) is True
    assert _active(plan, TRV) is False
    assert "electric preferred" in plan.reason


def test_large_gap_escalates_to_both_without_flag():
    """Baseline: a gap past primary_delta * 2 + hysteresis runs both sources."""
    plan = _plan(_room(prefer_electric=False), current_temp=17.0, target_temp=21.0)
    assert plan.active_sources == "both"
    assert _active(plan, TRV) is True
    assert _active(plan, AC) is True


def test_large_gap_does_not_escalate_with_flag():
    """The flag must suppress escalation, or a boost would still fire the boiler."""
    plan = _plan(_room(prefer_electric=True), current_temp=17.0, target_temp=21.0)
    assert plan.active_sources == "secondary"
    assert _active(plan, AC) is True
    assert _active(plan, TRV) is False


def test_flag_ignored_when_ac_disabled_by_extreme_cold():
    """Hardware protection wins: below ac_min_outdoor the room still gets boiler heat."""
    plan = _plan(_room(prefer_electric=True), outdoor_temp=-20.0)
    assert plan.active_sources == "primary"
    assert _active(plan, TRV) is True


def test_flag_holds_across_previous_state():
    """Weather hysteresis must not drag the room back to the boiler while the flag is on."""
    for previous in ("primary", "secondary", "both", "none"):
        plan = _plan(_room(prefer_electric=True), outdoor_temp=5.0, previous=previous)
        assert plan.active_sources == "secondary", previous


def test_defaults_off_leaves_behaviour_unchanged():
    """A room config without the key behaves exactly as before."""
    room = _room(prefer_electric=False)
    del room["prefer_electric_heat"]
    plan = _plan(room, outdoor_temp=5.0)
    assert plan.active_sources == "primary"


# --- Rooms with a resistive electric heater and no AC (bathroom / WC shape) ---

ELECTRIC = "climate.plug_heater"


def _electric_room(prefer_electric: bool, with_ac: bool = False):
    """Room shaped like the bathroom: boiler TRV plus a plug heater, no AC."""
    devices = [
        {"entity_id": TRV, "type": "trv", "role": "auto", "heating_system_type": "underfloor"},
        {"entity_id": ELECTRIC, "type": "electric", "role": "auto", "heating_system_type": ""},
    ]
    if with_ac:
        devices.append({"entity_id": AC, "type": "ac", "role": "auto", "heating_system_type": ""})
    room = _room(prefer_electric)
    room["devices"] = devices
    return room


def test_electric_room_orchestrates_without_an_ac():
    """A TRV + electric heater room must orchestrate; previously it bailed on no ACs."""
    plan = _plan(_electric_room(prefer_electric=True), outdoor_temp=5.0)
    assert plan is not None
    assert plan.active_sources == "secondary"
    assert _active(plan, ELECTRIC) is True
    assert _active(plan, TRV) is False


def test_electric_idle_when_flag_off():
    """Resistive heat is only worth running on surplus, so it must not be picked on
    cost heuristics - with the flag off the boiler heats and the heater stays idle."""
    plan = _plan(_electric_room(prefer_electric=False), outdoor_temp=5.0)
    assert plan.active_sources == "primary"
    assert _active(plan, TRV) is True
    assert _active(plan, ELECTRIC) is False


def test_electric_not_escalated_on_large_gap():
    """Even a large gap must not co-fire the boiler with the plug heater."""
    plan = _plan(_electric_room(prefer_electric=True), current_temp=17.0, target_temp=21.0)
    assert plan.active_sources == "secondary"
    assert _active(plan, ELECTRIC) is True
    assert _active(plan, TRV) is False


def test_electric_survives_extreme_cold():
    """The AC cold cutoff is compressor protection and must not disable resistive heat."""
    plan = _plan(_electric_room(prefer_electric=True), outdoor_temp=-20.0)
    assert plan.active_sources == "secondary"
    assert _active(plan, ELECTRIC) is True


def test_unselected_electric_still_gets_an_idle_command():
    """Regression: with the flag off the heater must be explicitly commanded off,
    or it stays on from the previous surplus window with nothing to turn it off."""
    plan = _plan(_electric_room(prefer_electric=False), outdoor_temp=5.0)
    cmd = next(c for c in plan.commands if c.entity_id == ELECTRIC)
    assert cmd.active is False
    assert cmd.power_fraction == 0.0


def test_unselected_electric_gets_idle_command_at_target():
    """Same, on the delta_t <= 0 early-exit path."""
    plan = _plan(_electric_room(prefer_electric=False), current_temp=22.0, target_temp=21.0)
    assert plan.active_sources == "none"
    cmd = next(c for c in plan.commands if c.entity_id == ELECTRIC)
    assert cmd.active is False


def test_electric_and_ac_both_secondary():
    """With both electric sources present, both are available to the secondary group."""
    plan = _plan(_electric_room(prefer_electric=True, with_ac=True), outdoor_temp=5.0)
    assert plan.active_sources == "secondary"
    assert _active(plan, ELECTRIC) is True
    assert _active(plan, AC) is True
    assert _active(plan, TRV) is False
