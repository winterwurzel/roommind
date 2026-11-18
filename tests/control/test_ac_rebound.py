"""AC rebound (air/mass offset of an AC room): charge helpers, RC model, EKF, optimizer."""

from __future__ import annotations

import math

import pytest

from custom_components.roommind.const import AC_REBOUND_TAU_MINUTES, MIN_POWER_FRACTION, MODE_COOLING, MODE_IDLE
from custom_components.roommind.control.mpc_optimizer import MPCOptimizer, MPCPlan
from custom_components.roommind.control.residual_heat import advance_rebound, mean_rebound
from custom_components.roommind.control.thermal_model import RCModel, ThermalEKF

# ---------------------------------------------------------------------------
# Charge helpers
# ---------------------------------------------------------------------------


def test_advance_rebound_charges_towards_one_while_cooling():
    charge = advance_rebound(0.0, True, AC_REBOUND_TAU_MINUTES)
    assert charge == pytest.approx(1.0 - math.exp(-1.0))
    assert advance_rebound(charge, True, 10 * AC_REBOUND_TAU_MINUTES) == pytest.approx(1.0, abs=1e-4)


def test_advance_rebound_decays_when_not_cooling():
    assert advance_rebound(0.8, False, AC_REBOUND_TAU_MINUTES) == pytest.approx(0.8 * math.exp(-1.0))


def test_advance_rebound_cutoff_and_zero_dt():
    assert advance_rebound(0.5, False, 0.0) == 0.5
    assert advance_rebound(0.5, False, 20 * AC_REBOUND_TAU_MINUTES) == 0.0


def test_mean_rebound_is_the_interval_mean():
    dt = 5.0
    # numeric mean of the closed-form trajectory
    n = 1000
    samples = [advance_rebound(0.6, False, dt * (i + 0.5) / n) for i in range(n)]
    assert mean_rebound(0.6, False, dt) == pytest.approx(sum(samples) / n, rel=1e-3)
    cooling = [advance_rebound(0.2, True, dt * (i + 0.5) / n) for i in range(n)]
    assert mean_rebound(0.2, True, dt) == pytest.approx(sum(cooling) / n, rel=1e-3)
    assert mean_rebound(0.3, True, 0.0) == 0.3


# ---------------------------------------------------------------------------
# RCModel
# ---------------------------------------------------------------------------


def _model(q_fast: float = 20.0) -> RCModel:
    return RCModel(C=1.0, U=0.02, Q_heat=3.0, Q_cool=2.0, Q_fast=q_fast)


def test_predict_without_charge_ignores_q_fast():
    model = _model()
    plain = RCModel(C=1.0, U=0.02, Q_heat=3.0, Q_cool=2.0)
    assert model.predict(24.0, 30.0, -2.0, 5.0) == pytest.approx(plain.predict(24.0, 30.0, -2.0, 5.0))
    assert model.predict(24.0, 30.0, 0.0, 5.0) == pytest.approx(plain.predict(24.0, 30.0, 0.0, 5.0))


def test_predict_rebound_warms_idle_room():
    model = _model()
    assert model.predict(24.0, 30.0, 0.0, 5.0, q_rebound=0.5) > model.predict(24.0, 30.0, 0.0, 5.0, q_rebound=0.0)


def test_predict_cooling_start_pulls_air_down():
    model = _model()
    fresh = model.predict(24.0, 30.0, -2.0, 5.0, q_rebound=0.1)
    charged = model.predict(24.0, 30.0, -2.0, 5.0, q_rebound=1.0)
    # a fresh run drops the air faster; a fully charged run only cools the whole room
    assert fresh < charged
    assert charged == pytest.approx(_model(0.0).predict(24.0, 30.0, -2.0, 5.0, q_rebound=1.0))


def test_rc_model_serializes_q_fast():
    model = RCModel.from_dict(_model(12.5).to_dict())
    assert model.Q_fast == 12.5
    assert RCModel.from_dict({"U": 0.1}).Q_fast == 0.0
    assert "Q_fast=12.5" in repr(_model(12.5))


# ---------------------------------------------------------------------------
# EKF
# ---------------------------------------------------------------------------


def _two_mode_truth(cycles: int) -> list[tuple[str, float, float]]:
    """Air temperature of a room with a fast air node: (mode, T_measured, q_rebound) per 3 min."""
    alpha, beta_c, beta_f, t_out = 0.015, 2.0, 20.0, 30.0
    T, charge, dt = 24.5, 0.0, 3.0
    out = []
    for c in range(cycles):
        for step in range(20):
            cooling = step < 2 + c % 3
            q = mean_rebound(charge, cooling, dt)
            u = -beta_c * (1.0 if cooling else 0.0) + beta_f * (q - (1.0 if cooling else 0.0))
            T_eq = t_out + u / alpha
            T = T_eq + (T - T_eq) * math.exp(-alpha * dt / 60.0)
            charge = advance_rebound(charge, cooling, dt)
            out.append((MODE_COOLING if cooling else MODE_IDLE, T, q))
    return out


def test_ekf_learns_air_offset_instead_of_a_huge_loss_rate():
    ekf = ThermalEKF(T_init=24.5)
    ekf.update(24.5, 30.0, MODE_IDLE, 3.0, q_rebound=0.0)
    for mode, T, q in _two_mode_truth(200):
        ekf.update(T, 30.0, mode, 3.0, q_rebound=q)
    assert ekf._x[6] > 10.0
    assert ekf._x[1] < 0.05
    assert ekf._x[3] < 6.0


def test_ekf_without_charge_leaves_beta_f_alone():
    ekf = ThermalEKF(T_init=24.5)
    ekf.update(24.5, 30.0, MODE_IDLE, 3.0)
    for mode, T, _q in _two_mode_truth(20):
        ekf.update(T, 30.0, mode, 3.0)
    assert ekf._x[6] == ThermalEKF._DEFAULT_BETA_F
    assert ekf._P[6][6] == pytest.approx(ThermalEKF._P_INIT_BETA_F)


def test_ekf_linearized_branch_has_fast_term():
    ekf = ThermalEKF()
    ekf._x[6] = 10.0
    F = ekf._compute_jacobian(24.0, 0.001, 0.0, 30.0, 0.05, MODE_IDLE, q_rebound=0.4)
    assert F[0][6] == pytest.approx(0.4 * 0.05)


def test_ekf_persists_beta_f_outside_the_6d_layout():
    ekf = ThermalEKF()
    ekf._x[6] = 17.5
    ekf._P[6][0] = ekf._P[0][6] = 0.3
    data = ekf.to_dict()
    # an older release only accepts 5 or 6 entries
    assert len(data["x"]) == 6
    assert len(data["P"]) == 6 and all(len(row) == 6 for row in data["P"])
    restored = ThermalEKF.from_dict(data)
    assert restored._x[6] == pytest.approx(17.5)
    assert restored._P[6][0] == pytest.approx(0.3)
    assert restored._P[0][6] == pytest.approx(0.3)
    assert restored._x[:6] == pytest.approx(ekf._x[:6])


def test_ekf_from_6d_data_keeps_learned_values():
    data = {
        "ekf_version": 6,
        "x": [24.0, 0.53, 3.0, 21.8, 3.1, 0.14],
        "P": [[0.1 if i == j else 0.0 for j in range(6)] for i in range(6)],
        "n_updates": 5000,
    }
    ekf = ThermalEKF.from_dict(data)
    assert ekf._x[:6] == pytest.approx(data["x"])
    assert ekf._x[6] == ThermalEKF._DEFAULT_BETA_F
    assert ekf._P[6][6] == ThermalEKF._P_INIT_BETA_F
    assert ekf._P[3][3] == pytest.approx(0.1)


def test_ekf_from_dict_accepts_full_7d_and_ignores_malformed_beta_f():
    ekf = ThermalEKF()
    ekf._x[6] = 9.0
    full = {"x": list(ekf._x), "P": [list(r) for r in ekf._P], "beta_f": {"x": "bad"}}
    assert ThermalEKF.from_dict(full)._x[6] == pytest.approx(9.0)
    short_row = {"x": list(ekf._x[:6]), "P": [list(r[:6]) for r in ekf._P[:6]], "beta_f": {"x": 5.0, "P": [1.0]}}
    assert ThermalEKF.from_dict(short_row)._x[6] == ThermalEKF._DEFAULT_BETA_F


def test_recovery_reset_restores_beta_f_default():
    data = {
        "ekf_version": 5,
        "x": [20.0, 2.0, 3.0, 4.0, 0.5, 0.3],
        "P": [[0.5 if i == j else 0.0 for j in range(6)] for i in range(6)],
        "beta_f": {"x": 30.0, "P": [0.0] * 6 + [1.0]},
        "n_updates": 100,
    }
    ekf = ThermalEKF.from_dict(data)
    assert ekf._x[6] == ThermalEKF._DEFAULT_BETA_F
    assert ekf._P[6][6] == ThermalEKF._P_INIT_BETA_F


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------


def _optimizer(q_fast: float = 20.0) -> MPCOptimizer:
    return MPCOptimizer(model=_model(q_fast), can_heat=False, can_cool=True, outdoor_cooling_min=10.0)


def test_optimizer_without_charge_is_unchanged():
    kwargs = {
        "T_room": 25.0,
        "T_outdoor_series": [30.0] * 24,
        "heat_target_series": [20.0] * 24,
        "cool_target_series": [24.0] * 24,
    }
    with_fast = _optimizer().optimize(**kwargs)
    without = _optimizer(0.0).optimize(**kwargs)
    assert with_fast.actions == without.actions
    assert with_fast.temperatures == without.temperatures


def test_plan_rebound_decays_and_only_warms_idle_blocks():
    """Like residual heat: the charge decays over the plan, cooling blocks neither recharge nor pull down."""
    opt = _optimizer()
    plan = MPCPlan(actions=[MODE_COOLING] + [MODE_IDLE] * 5, temperatures=[], power_fractions=[1.0] + [0.0] * 5)
    charged = opt.simulate_plan(plan, 24.0, [30.0] * 6, 5.0, rebound_charge=0.8)
    plain = opt.simulate_plan(plan, 24.0, [30.0] * 6, 5.0)
    assert charged[1] == plain[1]
    rises = [charged[i + 1] - charged[i] - (plain[i + 1] - plain[i]) for i in range(1, 6)]
    assert rises[0] > 0
    assert rises == sorted(rises, reverse=True)
    # without a charge nothing changes, also not with a learned Q_fast
    assert opt.simulate_plan(plan, 24.0, [30.0] * 6, 5.0, rebound_charge=0.0) == plain


def test_optimizer_carries_charge_through_the_plan():
    opt = _optimizer()
    plan = opt.optimize(
        T_room=25.0,
        T_outdoor_series=[30.0] * 24,
        heat_target_series=[20.0] * 24,
        cool_target_series=[24.0] * 24,
        rebound_charge=0.0,
    )
    assert plan.actions[0] == MODE_COOLING
    assert len(plan.temperatures) == 25


def test_closed_form_idle_drift_includes_rebound():
    opt = _optimizer()
    # at target, outdoors equal: only the rebound pushes the room up, so cooling is needed
    pf, mode = opt.compute_optimal_power(24.0, 24.0, 24.0, 5.0, rebound_charge=0.8)
    assert mode == MODE_COOLING and pf > 0
    pf_none, mode_none = opt.compute_optimal_power(24.0, 24.0, 24.0, 5.0)
    assert mode_none != MODE_COOLING or pf_none < pf


def test_closed_form_cooling_counters_the_rebound():
    opt = _optimizer()
    pf_charged, mode = opt.compute_optimal_power(24.02, 30.0, 24.0, 5.0, rebound_charge=0.05)
    pf_plain, _ = opt.compute_optimal_power(24.02, 30.0, 24.0, 5.0)
    assert mode == MODE_COOLING
    assert pf_plain >= MIN_POWER_FRACTION
    assert pf_charged > pf_plain


# ---------------------------------------------------------------------------
# Controller pass-through
# ---------------------------------------------------------------------------


def _controller(q_rebound: float | None):
    from custom_components.roommind.control.mpc_controller import MPCController
    from custom_components.roommind.control.thermal_model import RoomModelManager

    from .conftest import build_hass, make_room

    mgr = RoomModelManager()
    est = mgr.get_estimator("living_room")
    est._x[1], est._x[3], est._x[6] = 0.02, 2.0, 20.0
    return MPCController(
        build_hass(),
        make_room(thermostats=[], acs=["climate.ac"], climate_mode="cool_only"),
        model_manager=mgr,
        outdoor_temp=30.0,
        settings={},
        has_external_sensor=True,
        q_rebound=q_rebound,
    )


def test_controller_idle_drift_includes_rebound():
    assert _controller(0.8)._predict_idle_drift(24.0, 10.0) > _controller(None)._predict_idle_drift(24.0, 10.0)


def test_controller_hands_charge_to_optimizer_and_closed_form():
    from unittest.mock import patch

    from custom_components.roommind.control.mpc_controller import TargetTemps

    ctrl = _controller(0.4)
    with patch.object(MPCOptimizer, "optimize", autospec=True, side_effect=MPCOptimizer.optimize) as opt:
        ctrl._evaluate_mpc(25.0, TargetTemps(heat=None, cool=24.0))
    assert opt.call_args.kwargs["rebound_charge"] == 0.4
    optimizer = MPCOptimizer(model=ctrl._model_manager.get_model("living_room"), can_heat=False, can_cool=True)
    with patch.object(MPCOptimizer, "compute_optimal_power", return_value=(0.5, MODE_COOLING)) as cop:
        ctrl._gentle_power(optimizer, 25.0, 30.0, MODE_COOLING, 24.0)
    assert cop.call_args.kwargs["rebound_charge"] == 0.4


def test_horizon_counts_the_air_pull_down():
    ctrl = _controller(None)
    model = ctrl._model_manager.get_model("living_room")
    slow = RCModel(C=1.0, U=0.02, Q_heat=0.5, Q_cool=2.0)
    assert ctrl._compute_horizon_blocks(model, 30.0, 24.0) <= ctrl._compute_horizon_blocks(slow, 30.0, 24.0)
