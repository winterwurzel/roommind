"""Tests for weather forecast, cloud coverage, solar peak estimation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.managers.weather_manager import WeatherManager

from .conftest import (
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    make_mock_states_get,
)


class TestExtractCloudSeries:
    """Tests for _extract_cloud_series."""

    def test_empty_forecast(self, hass, mock_config_entry):
        _create_coordinator(hass, mock_config_entry)
        assert WeatherManager.extract_cloud_series([]) is None

    def test_all_none_cloud(self, hass, mock_config_entry):
        _create_coordinator(hass, mock_config_entry)
        forecast = [{"temperature": 5}, {"temperature": 6}]
        assert WeatherManager.extract_cloud_series(forecast) is None

    def test_some_valid_cloud(self, hass, mock_config_entry):
        _create_coordinator(hass, mock_config_entry)
        forecast = [
            {"temperature": 5, "cloud_coverage": 50},
            {"temperature": 6},
            {"temperature": 7, "cloud_coverage": 80},
        ]
        result = WeatherManager.extract_cloud_series(forecast)
        assert result == [50.0, None, 80.0]


class TestConvertForecastTemps:
    """Tests for _convert_forecast_temps."""

    def test_with_temperature(self, hass, mock_config_entry):
        coordinator = _create_coordinator(hass, mock_config_entry)
        forecasts = [{"temperature": 5.0, "other": "val"}, {"temperature": 10.0}]
        result = coordinator._weather_manager._convert_forecast_temps(forecasts)
        assert result[0]["temperature"] == 5.0
        assert result[0]["other"] == "val"
        assert result[1]["temperature"] == 10.0

    def test_without_temperature(self, hass, mock_config_entry):
        coordinator = _create_coordinator(hass, mock_config_entry)
        forecasts = [{"cloud_coverage": 50}]
        result = coordinator._weather_manager._convert_forecast_temps(forecasts)
        assert result == [{"cloud_coverage": 50}]


class TestReadWeatherForecast:
    """Tests for _read_weather_forecast."""

    @pytest.mark.asyncio
    async def test_no_weather_entity(self, hass, mock_config_entry):
        coordinator = _create_coordinator(hass, mock_config_entry)
        result = await coordinator._weather_manager.async_read_forecast({})
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_weather_entity(self, hass, mock_config_entry):
        coordinator = _create_coordinator(hass, mock_config_entry)
        result = await coordinator._weather_manager.async_read_forecast({"weather_entity": ""})
        assert result == []

    @pytest.mark.asyncio
    async def test_modern_service_success(self, hass, mock_config_entry):
        coordinator = _create_coordinator(hass, mock_config_entry)
        hass.services.async_call = AsyncMock(
            return_value={
                "weather.home": {
                    "forecast": [
                        {"temperature": 5.0, "cloud_coverage": 50},
                        {"temperature": 6.0, "cloud_coverage": 80},
                    ]
                }
            }
        )
        result = await coordinator._weather_manager.async_read_forecast({"weather_entity": "weather.home"})
        assert len(result) == 2
        assert result[0]["temperature"] == 5.0

    @pytest.mark.asyncio
    async def test_modern_service_fails_fallback_to_state(self, hass, mock_config_entry):
        coordinator = _create_coordinator(hass, mock_config_entry)
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("not supported"))
        state = MagicMock()
        state.attributes = {"forecast": [{"temperature": 7.0}]}
        hass.states.get = MagicMock(return_value=state)

        result = await coordinator._weather_manager.async_read_forecast({"weather_entity": "weather.home"})
        assert len(result) == 1
        assert result[0]["temperature"] == 7.0

    @pytest.mark.asyncio
    async def test_fallback_state_is_none(self, hass, mock_config_entry):
        coordinator = _create_coordinator(hass, mock_config_entry)
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("fail"))
        available_state = MagicMock()
        available_state.state = "cloudy"
        hass.states.get = MagicMock(side_effect=[available_state, None])

        result = await coordinator._weather_manager.async_read_forecast({"weather_entity": "weather.home"})
        assert result == []

    @pytest.mark.asyncio
    async def test_fallback_no_forecast_attribute(self, hass, mock_config_entry):
        coordinator = _create_coordinator(hass, mock_config_entry)
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("fail"))
        state = MagicMock()
        state.attributes = {}
        hass.states.get = MagicMock(return_value=state)

        result = await coordinator._weather_manager.async_read_forecast({"weather_entity": "weather.home"})
        assert result == []


class TestCoverageGaps:
    """Tests covering uncovered coordinator lines."""

    @pytest.mark.asyncio
    async def test_cloud_coverage_read_from_weather_entity(self, hass, mock_config_entry):
        """Cloud coverage is read from weather entity attributes."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {"weather_entity": "weather.home"}
        hass.data = {"roommind": {"store": store}}

        weather_state = MagicMock()
        weather_state.attributes = {"cloud_coverage": 75}

        hass.states.get = MagicMock(
            side_effect=make_mock_states_get(
                extra={"weather.home": ("sunny", {"cloud_coverage": 75})},
            )
        )
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        # Verify solar gain is a non-negative float
        assert isinstance(coordinator._current_q_solar, float)
        assert coordinator._current_q_solar >= 0
