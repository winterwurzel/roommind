"""Global control_mode setting: "bangbang" disables MPC decisions, "mpc" (default) keeps them."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.roommind.const import (
    CONTROL_MODE_BANGBANG,
    CONTROL_MODE_MPC,
    DEFAULT_CONTROL_MODE,
    MODE_HEATING,
    MODE_IDLE,
    TargetTemps,
)
from custom_components.roommind.control.mpc_controller import (
    MPCController,
    is_mpc_active,
    is_mpc_enabled,
)
from custom_components.roommind.control.thermal_model import RCModel, RoomModelManager

from .conftest import build_hass, make_room


def _trained_manager() -> RoomModelManager:
    mgr = RoomModelManager()
    mgr.update("living_room", 20.0, 5.0, "idle", 5.0)
    mgr.get_mode_counts = MagicMock(return_value=(100, 30, 0))
    mgr.get_prediction_std = MagicMock(return_value=0.1)
    mgr.get_model = MagicMock(return_value=RCModel(C=1.0, U=0.15, Q_heat=3.0, Q_cool=4.0))
    return mgr


def _controller(settings: dict | None, **kwargs) -> MPCController:
    return MPCController(
        build_hass(),
        make_room(),
        model_manager=kwargs.pop("model_manager", _trained_manager()),
        outdoor_temp=5.0,
        settings=settings,
        has_external_sensor=kwargs.pop("has_external_sensor", True),
        **kwargs,
    )


def test_default_is_mpc():
    assert DEFAULT_CONTROL_MODE == CONTROL_MODE_MPC


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (None, True),
        ({}, True),
        ({"control_mode": CONTROL_MODE_MPC}, True),
        ({"control_mode": CONTROL_MODE_BANGBANG}, False),
    ],
)
def test_is_mpc_enabled(settings, expected):
    assert is_mpc_enabled(settings) is expected


def test_is_mpc_active_false_in_bangbang_even_when_trained():
    mgr = _trained_manager()
    assert is_mpc_active(mgr, "living_room", True, False, 20.0, 5.0) is True
    assert is_mpc_active(mgr, "living_room", True, False, 20.0, 5.0, settings={"control_mode": "mpc"}) is True
    assert is_mpc_active(mgr, "living_room", True, False, 20.0, 5.0, settings={"control_mode": "bangbang"}) is False


@pytest.mark.asyncio
async def test_bangbang_never_runs_the_optimizer():
    ctrl = _controller({"control_mode": CONTROL_MODE_BANGBANG})
    ctrl._evaluate_mpc = MagicMock(side_effect=AssertionError("MPC must not run in bangbang mode"))

    mode, pf = await ctrl.async_evaluate(17.0, TargetTemps(heat=21.0, cool=24.0))

    assert (mode, pf) == (MODE_HEATING, 1.0)
    assert ctrl.last_plan is None
    assert ctrl.predicted_peak_temp is None


@pytest.mark.asyncio
async def test_bangbang_idle_inside_hysteresis():
    """From idle, bang-bang waits for the hysteresis; it reports zero power."""
    ctrl = _controller({"control_mode": CONTROL_MODE_BANGBANG})
    ctrl._evaluate_mpc = MagicMock(side_effect=AssertionError("MPC must not run in bangbang mode"))

    mode, pf = await ctrl.async_evaluate(20.95, TargetTemps(heat=21.0, cool=24.0))

    assert (mode, pf) == (MODE_IDLE, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [None, {}, {"control_mode": CONTROL_MODE_MPC}])
async def test_mpc_mode_and_default_use_the_optimizer(settings):
    ctrl = _controller(settings)
    ctrl._evaluate_mpc = MagicMock(return_value=(MODE_HEATING, 0.4))

    mode, pf = await ctrl.async_evaluate(20.0, TargetTemps(heat=21.0, cool=24.0))

    assert (mode, pf) == (MODE_HEATING, 0.4)
    ctrl._evaluate_mpc.assert_called_once()


@pytest.mark.asyncio
async def test_mpc_mode_still_falls_back_to_bangbang_while_learning():
    mgr = _trained_manager()
    mgr.get_prediction_std = MagicMock(return_value=0.9)
    ctrl = _controller({"control_mode": CONTROL_MODE_MPC}, model_manager=mgr)
    ctrl._evaluate_mpc = MagicMock(side_effect=AssertionError("model not confident"))

    mode, pf = await ctrl.async_evaluate(17.0, TargetTemps(heat=21.0, cool=24.0))

    assert (mode, pf) == (MODE_HEATING, 1.0)


@pytest.mark.asyncio
async def test_managed_mode_unaffected_by_control_mode():
    ctrl = _controller({"control_mode": CONTROL_MODE_BANGBANG}, has_external_sensor=False)

    mode, pf = await ctrl.async_evaluate(None, TargetTemps(heat=21.0, cool=24.0))

    assert (mode, pf) == (MODE_HEATING, 1.0)
