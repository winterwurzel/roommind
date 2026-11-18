"""Tests for outdoor temperature resolution and EKF training gating (#301).

The EKF must not train when no valid outdoor temperature is available: using
the room temperature as a degenerate fallback would drive ``alpha`` toward
the upper bound (time constant collapses to 30 min).  These tests cover:

  * ``_resolve_outdoor_temp`` priority: sensor → weather entity → none
  * EKF training is skipped when both sources are unavailable
  * The persistent notification fires once after the configured cycle count
    and clears once a valid outdoor reading returns
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.const import (
    OUTDOOR_UNAVAILABLE_NOTIFICATION_ID,
    OUTDOOR_UNAVAILABLE_NOTIFY_CYCLES,
)
from tests.coordinator.conftest import (
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    make_mock_states_get,
)


def _settings_with(**overrides):
    s = {"outdoor_temp_sensor": "sensor.outdoor_temp"}
    s.update(overrides)
    return s


# ---------------------------------------------------------------------------
# _resolve_outdoor_temp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outdoor_sensor_available_used(hass, mock_config_entry):
    """Sensor value wins over weather entity when both are available."""
    store = _make_store_mock(rooms={"living_room_abc12345": SAMPLE_ROOM})
    store.get_settings.return_value = _settings_with(weather_entity="weather.test")
    hass.data = {"roommind": {"store": store}}

    def states_get(eid):
        if eid == "sensor.outdoor_temp":
            s = MagicMock()
            s.state = "5.0"
            s.attributes = {}
            return s
        if eid == "weather.test":
            s = MagicMock()
            s.state = "sunny"
            s.attributes = {"temperature": 99.0}
            return s
        return make_mock_states_get()(eid)

    hass.states.get = MagicMock(side_effect=states_get)
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    await coordinator._async_update_data()

    assert coordinator.outdoor_temp == pytest.approx(5.0)
    assert coordinator.outdoor_temp_effective == pytest.approx(5.0)
    assert coordinator.outdoor_temp_source == "sensor"


@pytest.mark.asyncio
async def test_weather_fallback_when_sensor_missing(hass, mock_config_entry):
    """Weather entity is used when the sensor returns unavailable."""
    store = _make_store_mock(rooms={"living_room_abc12345": SAMPLE_ROOM})
    store.get_settings.return_value = _settings_with(
        outdoor_temp_sensor="",
        weather_entity="weather.test",
    )
    hass.data = {"roommind": {"store": store}}

    def states_get(eid):
        if eid == "weather.test":
            s = MagicMock()
            s.state = "cloudy"
            s.attributes = {"temperature": 8.5}
            return s
        return make_mock_states_get(outdoor_temp=None)(eid)

    hass.states.get = MagicMock(side_effect=states_get)
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    await coordinator._async_update_data()

    assert coordinator.outdoor_temp is None
    assert coordinator.outdoor_temp_effective == pytest.approx(8.5)
    assert coordinator.outdoor_temp_source == "weather"


@pytest.mark.asyncio
async def test_weather_unavailable_falls_through(hass, mock_config_entry):
    """An unavailable weather entity does not act as a source."""
    store = _make_store_mock(rooms={"living_room_abc12345": SAMPLE_ROOM})
    store.get_settings.return_value = _settings_with(
        outdoor_temp_sensor="",
        weather_entity="weather.test",
    )
    hass.data = {"roommind": {"store": store}}

    def states_get(eid):
        if eid == "weather.test":
            s = MagicMock()
            s.state = "unavailable"
            s.attributes = {"temperature": 8.5}
            return s
        return make_mock_states_get(outdoor_temp=None)(eid)

    hass.states.get = MagicMock(side_effect=states_get)
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    await coordinator._async_update_data()

    assert coordinator.outdoor_temp_effective is None
    assert coordinator.outdoor_temp_source == "none"


@pytest.mark.asyncio
async def test_both_none_returns_none(hass, mock_config_entry):
    """Neither sensor nor weather available → effective temperature is None."""
    store = _make_store_mock(rooms={"living_room_abc12345": SAMPLE_ROOM})
    store.get_settings.return_value = _settings_with(
        outdoor_temp_sensor="",
        weather_entity="",
    )
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(side_effect=make_mock_states_get(outdoor_temp=None))
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    await coordinator._async_update_data()

    assert coordinator.outdoor_temp is None
    assert coordinator.outdoor_temp_effective is None
    assert coordinator.outdoor_temp_source == "none"


# ---------------------------------------------------------------------------
# EKF training gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ekf_training_skipped_when_no_outdoor(hass, mock_config_entry):
    """No outdoor source → training process is not invoked; accumulator cleared."""
    store = _make_store_mock(rooms={"living_room_abc12345": SAMPLE_ROOM})
    store.get_settings.return_value = _settings_with(outdoor_temp_sensor="", weather_entity="")
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(side_effect=make_mock_states_get(outdoor_temp=None))
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    process_mock = MagicMock()
    clear_mock = MagicMock()
    coordinator._ekf_training.process = process_mock
    coordinator._ekf_training.clear = clear_mock

    await coordinator._async_update_data()

    assert not process_mock.called
    assert clear_mock.called
    # the model temperature goes stale without training: resync on resume (#478)
    assert clear_mock.call_args.kwargs["resync"] is True


@pytest.mark.asyncio
async def test_ekf_training_uses_weather_fallback(hass, mock_config_entry):
    """Weather fallback feeds the EKF with the weather temperature."""
    store = _make_store_mock(rooms={"living_room_abc12345": SAMPLE_ROOM})
    store.get_settings.return_value = _settings_with(
        outdoor_temp_sensor="",
        weather_entity="weather.test",
    )
    hass.data = {"roommind": {"store": store}}

    def states_get(eid):
        if eid == "weather.test":
            s = MagicMock()
            s.state = "cloudy"
            s.attributes = {"temperature": 4.0}
            return s
        return make_mock_states_get(outdoor_temp=None)(eid)

    hass.states.get = MagicMock(side_effect=states_get)
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    process_mock = MagicMock()
    coordinator._ekf_training.process = process_mock

    await coordinator._async_update_data()

    assert process_mock.called
    assert process_mock.call_args.kwargs["T_outdoor"] == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Notification lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notification_fires_after_threshold(hass, mock_config_entry, monkeypatch):
    """Notification raised once OUTDOOR_UNAVAILABLE_NOTIFY_CYCLES is reached."""
    create_mock = MagicMock()
    monkeypatch.setattr(
        "custom_components.roommind.coordinator.async_create_notification",
        create_mock,
    )
    monkeypatch.setattr(
        "custom_components.roommind.coordinator.async_dismiss_notification",
        MagicMock(),
    )

    store = _make_store_mock(rooms={"living_room_abc12345": SAMPLE_ROOM})
    store.get_settings.return_value = _settings_with(outdoor_temp_sensor="", weather_entity="")
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(side_effect=make_mock_states_get(outdoor_temp=None))
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)

    for _ in range(OUTDOOR_UNAVAILABLE_NOTIFY_CYCLES - 1):
        await coordinator._async_update_data()
    assert not create_mock.called, "should not notify before threshold"

    await coordinator._async_update_data()
    assert create_mock.called
    _, kwargs = create_mock.call_args.args, create_mock.call_args.kwargs
    assert kwargs["notification_id"] == OUTDOOR_UNAVAILABLE_NOTIFICATION_ID

    # Re-fire suppression
    await coordinator._async_update_data()
    assert create_mock.call_count == 1


@pytest.mark.asyncio
async def test_notification_disabled_by_setting(hass, mock_config_entry, monkeypatch):
    """outdoor_unavailable_notify=False suppresses the notification entirely."""
    create_mock = MagicMock()
    monkeypatch.setattr(
        "custom_components.roommind.coordinator.async_create_notification",
        create_mock,
    )

    store = _make_store_mock(rooms={"living_room_abc12345": SAMPLE_ROOM})
    store.get_settings.return_value = _settings_with(
        outdoor_temp_sensor="",
        weather_entity="",
        outdoor_unavailable_notify=False,
    )
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(side_effect=make_mock_states_get(outdoor_temp=None))
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    for _ in range(OUTDOOR_UNAVAILABLE_NOTIFY_CYCLES + 5):
        await coordinator._async_update_data()
    assert not create_mock.called


@pytest.mark.asyncio
async def test_notification_dismissed_when_outdoor_returns(hass, mock_config_entry, monkeypatch):
    """A returning outdoor reading dismisses the persistent notification and
    resets the cycle counter so a future outage can re-notify."""
    create_mock = MagicMock()
    dismiss_mock = MagicMock()
    monkeypatch.setattr(
        "custom_components.roommind.coordinator.async_create_notification",
        create_mock,
    )
    monkeypatch.setattr(
        "custom_components.roommind.coordinator.async_dismiss_notification",
        dismiss_mock,
    )

    store = _make_store_mock(rooms={"living_room_abc12345": SAMPLE_ROOM})
    settings = _settings_with(outdoor_temp_sensor="sensor.outdoor_temp", weather_entity="")
    store.get_settings.return_value = settings
    hass.data = {"roommind": {"store": store}}

    outdoor_value = {"v": None}

    def states_get(eid):
        if eid == "sensor.outdoor_temp":
            if outdoor_value["v"] is None:
                return None
            s = MagicMock()
            s.state = str(outdoor_value["v"])
            s.attributes = {}
            return s
        return make_mock_states_get(outdoor_temp=None)(eid)

    hass.states.get = MagicMock(side_effect=states_get)
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    for _ in range(OUTDOOR_UNAVAILABLE_NOTIFY_CYCLES):
        await coordinator._async_update_data()
    assert create_mock.called

    # Outdoor returns → dismiss
    outdoor_value["v"] = 6.0
    await coordinator._async_update_data()
    assert dismiss_mock.called
    assert coordinator._outdoor_unavailable_cycles == 0
    assert coordinator._outdoor_warning_sent is False
