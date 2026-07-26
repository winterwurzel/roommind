"""Schema tests bound to the *real* websocket_save_room schema.

tests/test_websocket_api.py rebuilds the device sub-schema by hand, so it keeps
passing when the real schema and the copy drift apart - which is exactly how a
new device type can reach the frontend while the backend still rejects it on
save. These tests validate against ``websocket_save_room._ws_schema`` so they
fail if the real schema stops accepting a supported type.
"""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.roommind.utils.device_utils import VALID_DEVICE_TYPES
from custom_components.roommind.websocket_api import (
    _validate_device_idle_action,
    websocket_save_room,
)

SCHEMA = websocket_save_room._ws_schema


def _msg(device: dict) -> dict:
    return {
        "id": 1,
        "type": "roommind/rooms/save",
        "area_id": "bathroom",
        "devices": [device],
    }


@pytest.mark.parametrize("device_type", sorted(VALID_DEVICE_TYPES))
def test_every_valid_device_type_is_accepted(device_type):
    """Anything in VALID_DEVICE_TYPES must survive a save round-trip.

    Regression: "electric" was added to VALID_DEVICE_TYPES and the frontend
    picker, but the websocket schema still hardcoded ["trv", "ac"], so choosing
    Electric Heater in the UI failed with "Failed to save configuration".
    """
    SCHEMA(_msg({"entity_id": "climate.x", "type": device_type}))


def test_unknown_device_type_still_rejected():
    with pytest.raises(vol.Invalid):
        SCHEMA(_msg({"entity_id": "climate.x", "type": "heat_pump"}))


def test_electric_rejects_fan_only_idle_action():
    """A resistive heater has no fan."""
    with pytest.raises(vol.Invalid):
        _validate_device_idle_action({"type": "electric", "idle_action": "fan_only"})


def test_electric_accepts_default_idle_action():
    device = {"type": "electric", "idle_action": "off"}
    assert _validate_device_idle_action(device) is device
