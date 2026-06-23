"""Tests for WeatherManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import UnitOfTemperature

from custom_components.roommind.managers.weather_manager import WeatherManager


def _make_hass(fahrenheit: bool = False) -> MagicMock:
    hass = MagicMock()
    hass.config.units.temperature_unit = UnitOfTemperature.FAHRENHEIT if fahrenheit else UnitOfTemperature.CELSIUS
    hass.states.get = MagicMock(return_value=None)
    return hass


def _make_available_state() -> MagicMock:
    state = MagicMock()
    state.state = "cloudy"
    return state


@pytest.mark.asyncio
async def test_no_weather_entity_returns_empty():
    """When no weather_entity is configured, forecast is empty."""
    mgr = WeatherManager(_make_hass())
    result = await mgr.async_read_forecast({})
    assert result == []
    assert mgr.forecast == []


@pytest.mark.asyncio
async def test_forecast_entry_without_temperature():
    """Forecast entry without 'temperature' key is passed through unchanged."""
    hass = _make_hass()
    hass.states.get = MagicMock(return_value=_make_available_state())
    hass.services.async_call = AsyncMock(
        return_value={
            "weather.home": {
                "forecast": [
                    {"temperature": 10.0, "cloud_coverage": 50},
                    {"cloud_coverage": 80},  # no temperature key
                ]
            }
        }
    )

    mgr = WeatherManager(hass)
    result = await mgr.async_read_forecast({"weather_entity": "weather.home"})

    assert len(result) == 2
    assert result[0]["temperature"] == 10.0
    # Second entry has no temperature, should be passed through as-is
    assert "temperature" not in result[1]
    assert result[1]["cloud_coverage"] == 80


# --- extract_cloud_series ---


def test_extract_cloud_series_with_valid_data():
    """Forecast entries with cloud_coverage return list of floats."""
    forecast = [
        {"cloud_coverage": 20},
        {"cloud_coverage": 80},
        {"cloud_coverage": 50},
    ]
    result = WeatherManager.extract_cloud_series(forecast)
    assert result == [20.0, 80.0, 50.0]


def test_extract_cloud_series_empty_forecast():
    """Empty forecast list returns None."""
    result = WeatherManager.extract_cloud_series([])
    assert result is None


def test_extract_cloud_series_missing_key():
    """Entries without cloud_coverage key produce None in the series."""
    forecast = [
        {"cloud_coverage": 30},
        {"temperature": 10},  # no cloud_coverage
        {"cloud_coverage": 70},
    ]
    result = WeatherManager.extract_cloud_series(forecast)
    assert result == [30.0, None, 70.0]


def test_extract_cloud_series_all_missing_returns_none():
    """If no entry has cloud_coverage, returns None (clear-sky fallback)."""
    forecast = [
        {"temperature": 10},
        {"temperature": 12},
    ]
    result = WeatherManager.extract_cloud_series(forecast)
    assert result is None


@pytest.mark.asyncio
async def test_service_response_parsed_and_stored():
    """Successful get_forecasts service call returns converted forecast."""
    hass = _make_hass()
    hass.states.get = MagicMock(return_value=_make_available_state())
    hass.services.async_call = AsyncMock(
        return_value={"weather.home": {"forecast": [{"temperature": 10.0}, {"temperature": 12.0}]}}
    )

    mgr = WeatherManager(hass)
    result = await mgr.async_read_forecast({"weather_entity": "weather.home"})

    assert len(result) == 2
    assert result[0]["temperature"] == 10.0
    assert mgr.forecast == result


@pytest.mark.asyncio
async def test_service_response_converts_fahrenheit():
    """Forecast temperatures are converted from °F to °C when HA uses Fahrenheit."""
    hass = _make_hass(fahrenheit=True)
    hass.states.get = MagicMock(return_value=_make_available_state())
    hass.services.async_call = AsyncMock(
        return_value={
            "weather.home": {"forecast": [{"temperature": 50.0}]}  # 50°F = 10°C
        }
    )

    mgr = WeatherManager(hass)
    result = await mgr.async_read_forecast({"weather_entity": "weather.home"})

    assert abs(result[0]["temperature"] - 10.0) < 0.01


@pytest.mark.asyncio
async def test_service_failure_falls_back_to_state_attributes():
    """If get_forecasts service fails, falls back to state attributes."""
    hass = _make_hass()
    hass.services.async_call = AsyncMock(side_effect=Exception("service unavailable"))

    state = MagicMock()
    state.attributes = {"forecast": [{"temperature": 8.0}]}
    hass.states.get = MagicMock(return_value=state)

    mgr = WeatherManager(hass)
    result = await mgr.async_read_forecast({"weather_entity": "weather.home"})

    assert len(result) == 1
    assert result[0]["temperature"] == 8.0


@pytest.mark.asyncio
async def test_service_failure_no_state_returns_empty():
    """If service fails and state is unavailable, returns empty list."""
    hass = _make_hass()
    hass.services.async_call = AsyncMock(side_effect=Exception("unavailable"))
    hass.states.get = MagicMock(side_effect=[_make_available_state(), None])

    mgr = WeatherManager(hass)
    result = await mgr.async_read_forecast({"weather_entity": "weather.home"})

    assert result == []


@pytest.mark.asyncio
async def test_entity_missing_skips_service_call():
    """No state for the weather entity yet (e.g. HA startup) skips the service call (#326)."""
    hass = _make_hass()
    hass.services.async_call = AsyncMock()

    mgr = WeatherManager(hass)
    result = await mgr.async_read_forecast({"weather_entity": "weather.home"})

    assert result == []
    assert mgr.forecast == []
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_entity_unavailable_skips_service_call():
    """Unavailable weather entity skips the service call (#326)."""
    hass = _make_hass()
    hass.services.async_call = AsyncMock()
    state = MagicMock()
    state.state = "unavailable"
    hass.states.get = MagicMock(return_value=state)

    mgr = WeatherManager(hass)
    result = await mgr.async_read_forecast({"weather_entity": "weather.home"})

    assert result == []
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_entity_unknown_skips_service_call():
    """Weather entity in unknown state skips the service call (#326)."""
    hass = _make_hass()
    hass.services.async_call = AsyncMock()
    state = MagicMock()
    state.state = "unknown"
    hass.states.get = MagicMock(return_value=state)

    mgr = WeatherManager(hass)
    result = await mgr.async_read_forecast({"weather_entity": "weather.home"})

    assert result == []
    hass.services.async_call.assert_not_called()
