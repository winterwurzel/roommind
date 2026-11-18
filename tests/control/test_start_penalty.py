"""Start cost for slow heating systems: UFH no longer toggles heat/idle at the target (#429)."""

from __future__ import annotations

import pytest

from custom_components.roommind.const import MODE_HEATING, MODE_IDLE, SLOW_SYSTEM_START_PENALTY, TargetTemps
from custom_components.roommind.control.mpc_controller import MPCController
from custom_components.roommind.control.mpc_optimizer import MPCOptimizer, MPCPlan, slow_start_penalty
from custom_components.roommind.control.residual_heat import get_min_run_blocks
from custom_components.roommind.control.thermal_model import RCModel, RoomModelManager

from .conftest import build_hass, make_room


def _first_action(t_room: float, *, penalty: float, initial_mode: str = MODE_IDLE, outdoor: float = 19.0) -> str:
    """Slab-like room a few hundredths below its target with a mild outdoor load."""
    opt = MPCOptimizer(
        RCModel(C=1.0, U=0.02, Q_heat=1.5, Q_cool=0.0),
        can_heat=True,
        can_cool=False,
        min_run_blocks=6,
        heating_system_type="underfloor",
        start_penalty=penalty,
    )
    plan = opt.optimize(
        T_room=t_room,
        T_outdoor_series=[outdoor] * 24,
        heat_target_series=[21.5] * 24,
        cool_target_series=[30.0] * 24,
        dt_minutes=5,
        initial_mode=initial_mode,
    )
    return plan.actions[0]


def test_helper_charges_only_slow_systems():
    assert slow_start_penalty(get_min_run_blocks("underfloor")) == SLOW_SYSTEM_START_PENALTY
    assert slow_start_penalty(get_min_run_blocks("radiator")) == 0.0
    assert slow_start_penalty(get_min_run_blocks("")) == 0.0


def test_marginal_deficit_does_not_start_a_slow_system():
    assert _first_action(21.45, penalty=0.0) == MODE_HEATING
    assert _first_action(21.45, penalty=SLOW_SYSTEM_START_PENALTY) == MODE_IDLE


def test_continuing_a_running_slow_system_is_free():
    assert _first_action(21.45, penalty=SLOW_SYSTEM_START_PENALTY, initial_mode=MODE_HEATING) == MODE_HEATING


def test_real_demand_still_starts():
    assert _first_action(21.3, penalty=SLOW_SYSTEM_START_PENALTY) == MODE_HEATING
    assert _first_action(21.45, penalty=SLOW_SYSTEM_START_PENALTY, outdoor=10.0) == MODE_HEATING


@pytest.mark.parametrize("initial_mode", [MODE_IDLE, MODE_HEATING])
def test_without_penalty_the_running_mode_changes_nothing(initial_mode):
    opt = MPCOptimizer(RCModel(C=1.0, U=0.05, Q_heat=2.0, Q_cool=2.0), min_run_blocks=2)
    kwargs = {
        "T_room": 20.6,
        "T_outdoor_series": [8.0] * 24,
        "heat_target_series": [21.0] * 12 + [17.0] * 12,
        "cool_target_series": [24.0] * 24,
        "dt_minutes": 5,
    }
    assert opt.optimize(**kwargs, initial_mode=initial_mode) == opt.optimize(**kwargs)


def _controller(monkeypatch, *, system: str, previous_mode: str):
    seen: dict = {}

    def fake_optimize(self, **kwargs):
        seen["penalty"] = self.start_penalty
        seen["initial_mode"] = kwargs.get("initial_mode")
        return MPCPlan(actions=[MODE_IDLE] * 24, temperatures=[21.0] * 25, power_fractions=[0.0] * 24)

    monkeypatch.setattr(
        "custom_components.roommind.control.mpc_controller.MPCOptimizer.optimize",
        fake_optimize,
    )
    ctrl = MPCController(
        build_hass(),
        make_room(heating_system_type=system),
        model_manager=RoomModelManager(),
        outdoor_temp=5.0,
        settings={},
        has_external_sensor=True,
        heating_system_type=system,
        previous_mode=previous_mode,
    )
    ctrl._evaluate_mpc(21.0, TargetTemps(heat=21.5, cool=None))
    return seen


def test_controller_charges_starts_for_underfloor_and_passes_the_running_mode(monkeypatch):
    seen = _controller(monkeypatch, system="underfloor", previous_mode=MODE_HEATING)
    assert seen == {"penalty": SLOW_SYSTEM_START_PENALTY, "initial_mode": MODE_HEATING}


def test_controller_keeps_fast_systems_free_of_start_cost(monkeypatch):
    seen = _controller(monkeypatch, system="radiator", previous_mode=MODE_IDLE)
    assert seen == {"penalty": 0.0, "initial_mode": MODE_IDLE}


def test_cooling_starts_pay_no_slow_heating_penalty():
    """UFH+AC room in summer: the AC's cooling start is not charged the screed's start cost."""

    def first(penalty: float) -> str:
        opt = MPCOptimizer(
            RCModel(C=1.0, U=0.02, Q_heat=1.5, Q_cool=3.0),
            can_heat=True,
            can_cool=True,
            min_run_blocks=6,
            heating_system_type="underfloor",
            start_penalty=penalty,
        )
        plan = opt.optimize(
            T_room=24.55,
            T_outdoor_series=[30.0] * 24,
            heat_target_series=[21.0] * 24,
            cool_target_series=[24.5] * 24,
            dt_minutes=5,
        )
        return plan.actions[0]

    assert first(SLOW_SYSTEM_START_PENALTY) == first(0.0)
