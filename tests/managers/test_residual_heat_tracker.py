"""Tests for the ResidualHeatTracker manager."""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from custom_components.roommind.const import MODE_HEATING, MODE_IDLE
from custom_components.roommind.managers.residual_heat_tracker import ResidualHeatTracker

# ---------------------------------------------------------------------------
# update – cleanup branch (lines 40-43)
# ---------------------------------------------------------------------------


def test_update_clears_state_when_residual_zero_and_idle():
    """When mode is idle, previous was idle, and q_residual==0, state is cleaned up."""
    tracker = ResidualHeatTracker()
    # Seed some state as if heating stopped earlier
    tracker._off_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 900.0

    tracker.update("room1", MODE_IDLE, 0.0, MODE_IDLE, q_residual=0.0)

    assert "room1" not in tracker._off_since
    assert "room1" not in tracker._off_power
    assert "room1" not in tracker._on_since


def test_update_keeps_state_when_residual_nonzero():
    """When q_residual > 0, state should NOT be cleaned up."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 900.0

    tracker.update("room1", MODE_IDLE, 0.0, MODE_IDLE, q_residual=0.5)

    assert "room1" in tracker._off_since
    assert "room1" in tracker._off_power
    assert "room1" in tracker._on_since


def test_update_cleanup_no_state_is_noop():
    """Cleanup branch with no existing state should not raise."""
    tracker = ResidualHeatTracker()
    tracker.update("room1", MODE_IDLE, 0.0, MODE_IDLE, q_residual=0.0)
    assert "room1" not in tracker._off_since


# ---------------------------------------------------------------------------
# clear_room (line 53)
# ---------------------------------------------------------------------------


def test_clear_room_delegates_to_remove():
    """clear_room should remove all state for the given room."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 900.0

    tracker.clear_room("room1")

    assert "room1" not in tracker._off_since
    assert "room1" not in tracker._off_power
    assert "room1" not in tracker._on_since


# ---------------------------------------------------------------------------
# clear_all (lines 57-59)
# ---------------------------------------------------------------------------


def test_clear_all_removes_all_rooms():
    """clear_all should remove state for every room."""
    tracker = ResidualHeatTracker()
    for room in ("room1", "room2", "room3"):
        tracker._off_since[room] = 1000.0
        tracker._off_power[room] = 0.8
        tracker._on_since[room] = 900.0

    tracker.clear_all()

    assert len(tracker._off_since) == 0
    assert len(tracker._off_power) == 0
    assert len(tracker._on_since) == 0


# ---------------------------------------------------------------------------
# get_q_residual – core logic
# ---------------------------------------------------------------------------


def test_get_q_residual_no_state_returns_zero():
    """Room not tracked at all returns 0.0."""
    tracker = ResidualHeatTracker()
    result = tracker.get_q_residual("unknown_room", "radiator", MODE_IDLE)
    assert result == 0.0


def test_get_q_residual_no_off_since_returns_zero():
    """Room tracked (on_since set) but no off_since returns 0.0."""
    tracker = ResidualHeatTracker()
    tracker._on_since["room1"] = 1000.0
    result = tracker.get_q_residual("room1", "radiator", MODE_IDLE)
    assert result == 0.0


def test_get_q_residual_previous_mode_heating_returns_zero():
    """When previous_mode is HEATING, residual heat is always 0."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._on_since["room1"] = 900.0
    tracker._off_power["room1"] = 0.8
    result = tracker.get_q_residual("room1", "radiator", MODE_HEATING)
    assert result == 0.0


def test_get_q_residual_empty_system_type_returns_zero():
    """Empty system_type returns 0.0 (no residual heat without known system)."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._on_since["room1"] = 900.0
    result = tracker.get_q_residual("room1", "", MODE_IDLE)
    assert result == 0.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_computes_correctly_radiator(mock_time):
    """Verify computed residual heat matches compute_residual_heat for radiator."""
    now = 2000.0
    on_time = 1700.0  # started heating at t=1700
    off_time = 1900.0  # stopped heating at t=1900
    mock_time.time.return_value = now

    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = off_time
    tracker._on_since["room1"] = on_time
    tracker._off_power["room1"] = 0.7

    result = tracker.get_q_residual("room1", "radiator", MODE_IDLE)

    # Hardcoded expected value from the formula:
    #   elapsed = (2000-1900)/60 = 1.6667 min, heat_dur = (1900-1700)/60 = 3.3333 min
    #   radiator: tau=10, initial=0.3, tau_charge=15
    #   charge = 1 - exp(-3.3333/15) = 0.19927
    #   q = 0.3 * 0.19927 * exp(-1.6667/10) * 0.7 = ~0.03542
    import math

    charge = 1 - math.exp(-3.3333 / 15.0)
    expected = 0.3 * charge * math.exp(-1.6667 / 10.0) * 0.7
    assert result == pytest.approx(expected, abs=1e-6)
    assert result == pytest.approx(0.03542, abs=1e-3)


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_computes_correctly_underfloor(mock_time):
    """Verify computed residual heat matches compute_residual_heat for underfloor."""
    now = 5000.0
    on_time = 1000.0  # long heating run
    off_time = 4500.0
    mock_time.time.return_value = now

    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = off_time
    tracker._on_since["room1"] = on_time
    tracker._off_power["room1"] = 1.0

    result = tracker.get_q_residual("room1", "underfloor", MODE_IDLE)

    # Hardcoded expected value from the formula:
    #   elapsed = (5000-4500)/60 = 8.3333 min, heat_dur = (4500-1000)/60 = 58.3333 min
    #   underfloor: tau=90, initial=0.85, tau_charge=60
    #   charge = 1 - exp(-58.3333/60) = 0.62136
    #   q = 0.85 * 0.62136 * exp(-8.3333/90) * 1.0 = ~0.48137
    charge = 1 - math.exp(-58.3333 / 60.0)
    expected = 0.85 * charge * math.exp(-8.3333 / 90.0) * 1.0
    assert result == pytest.approx(expected, abs=1e-6)
    assert result == pytest.approx(0.4814, abs=1e-2)


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_no_on_since_uses_off_since_as_fallback(mock_time):
    """When _on_since is missing, heat_dur should be 0 (off_since - off_since)."""
    now = 2000.0
    off_time = 1900.0
    mock_time.time.return_value = now

    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = off_time
    # No _on_since set — fallback to off_since in .get()

    result = tracker.get_q_residual("room1", "radiator", MODE_IDLE)

    # heat_dur = 0 → charge_fraction = 1.0 (fully charged assumption)
    # elapsed = (2000-1900)/60 = 1.6667 min, pf defaults to 1.0
    # q = 0.3 * 1.0 * exp(-1.6667/10) * 1.0 = ~0.2539
    expected_val = 0.3 * math.exp(-1.6667 / 10.0)
    assert result == pytest.approx(expected_val, abs=1e-3)


# ---------------------------------------------------------------------------
# update – heating transitions
# ---------------------------------------------------------------------------


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_heating_mode_records_on_since(mock_time):
    """Starting heating (from non-heating) records _on_since."""
    mock_time.time.return_value = 5000.0
    tracker = ResidualHeatTracker()

    tracker.update("room1", MODE_HEATING, 0.6, MODE_IDLE)

    assert tracker._on_since["room1"] == 5000.0
    # off_since should be cleared (was never set, so just not present)
    assert "room1" not in tracker._off_since
    # power fraction recorded
    assert tracker._off_power["room1"] == 0.6


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_heating_continued_does_not_reset_on_since(mock_time):
    """Continued heating (previous was also HEATING) does not overwrite _on_since."""
    tracker = ResidualHeatTracker()
    tracker._on_since["room1"] = 3000.0  # original start time

    mock_time.time.return_value = 4000.0
    tracker.update("room1", MODE_HEATING, 0.8, MODE_HEATING)

    # on_since should remain the original value
    assert tracker._on_since["room1"] == 3000.0
    # power fraction updated
    assert tracker._off_power["room1"] == 0.8


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_heating_to_idle_transition(mock_time):
    """Transitioning from HEATING to IDLE records _off_since."""
    tracker = ResidualHeatTracker()
    # First: start heating
    mock_time.time.return_value = 1000.0
    tracker.update("room1", MODE_HEATING, 0.9, MODE_IDLE)
    assert tracker._on_since["room1"] == 1000.0

    # Then: stop heating
    mock_time.time.return_value = 2000.0
    tracker.update("room1", MODE_IDLE, 0.0, MODE_HEATING)

    assert tracker._off_since["room1"] == 2000.0
    # on_since preserved for duration calculation
    assert tracker._on_since["room1"] == 1000.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_idle_to_heating_clears_off_since(mock_time):
    """Re-starting heating after idle clears _off_since and sets new _on_since."""
    tracker = ResidualHeatTracker()
    # Establish off_since (as if heating stopped previously)
    tracker._off_since["room1"] = 1500.0
    tracker._on_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8

    # Now start heating again
    mock_time.time.return_value = 2000.0
    tracker.update("room1", MODE_HEATING, 0.7, MODE_IDLE)

    assert "room1" not in tracker._off_since
    assert tracker._on_since["room1"] == 2000.0
    assert tracker._off_power["room1"] == 0.7


# ---------------------------------------------------------------------------
# remove_room – all dicts cleared
# ---------------------------------------------------------------------------


def test_remove_room_clears_all_dicts():
    """remove_room clears all internal dicts for the given room."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 900.0
    # Other room should be unaffected
    tracker._off_since["room2"] = 2000.0
    tracker._off_power["room2"] = 0.5
    tracker._on_since["room2"] = 1800.0

    tracker.remove_room("room1")

    assert "room1" not in tracker._off_since
    assert "room1" not in tracker._off_power
    assert "room1" not in tracker._on_since
    # room2 untouched
    assert "room2" in tracker._off_since
    assert "room2" in tracker._off_power
    assert "room2" in tracker._on_since


# ---------------------------------------------------------------------------
# AcReboundTracker
# ---------------------------------------------------------------------------


def test_rebound_charge_rises_while_cooling_and_decays_after():
    from custom_components.roommind.const import AC_REBOUND_TAU_MINUTES, MODE_COOLING
    from custom_components.roommind.managers.residual_heat_tracker import AcReboundTracker

    tracker = AcReboundTracker()
    path = "custom_components.roommind.managers.residual_heat_tracker.time.time"
    tau_s = AC_REBOUND_TAU_MINUTES * 60
    with patch(path, return_value=1000.0):
        assert tracker.get_charge("room1") == 0.0
        tracker.update("room1", MODE_COOLING)
    with patch(path, return_value=1000.0 + tau_s):
        charged = tracker.get_charge("room1")
        assert charged == pytest.approx(1.0 - math.exp(-1.0))
        tracker.update("room1", MODE_IDLE)
    with patch(path, return_value=1000.0 + 2 * tau_s):
        assert tracker.get_charge("room1") == pytest.approx(charged * math.exp(-1.0))
    with patch(path, return_value=1000.0 + 30 * tau_s):
        tracker.update("room1", None)
    assert "room1" not in tracker._at


def test_rebound_tracker_remove_and_clear():
    from custom_components.roommind.const import MODE_COOLING
    from custom_components.roommind.managers.residual_heat_tracker import AcReboundTracker

    tracker = AcReboundTracker()
    tracker.update("a", MODE_COOLING)
    tracker.update("b", MODE_COOLING)
    tracker.remove_room("a")
    assert "a" not in tracker._at and "b" in tracker._at
    tracker.clear_all()
    assert not tracker._at and not tracker._charge and not tracker._cooling
