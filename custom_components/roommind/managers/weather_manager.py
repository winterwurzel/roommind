"""Weather forecast manager for RoomMind."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from ..utils.temp_utils import ha_temp_to_celsius

_LOGGER = logging.getLogger(__name__)


class WeatherManager:
    """Manages weather forecast retrieval and conversion."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._outdoor_forecast: list[dict] = []

    @property
    def forecast(self) -> list[dict]:
        """Return the current outdoor forecast."""
        return self._outdoor_forecast

    async def async_read_forecast(self, settings: dict) -> list[dict]:
        """Read weather forecast from configured weather entity."""
        weather_entity = settings.get("weather_entity", "")
        if not weather_entity:
            self._outdoor_forecast = []
            return []

        # Calling get_forecasts on a missing/unavailable entity makes HA core
        # log a warning (e.g. during startup before the weather integration
        # has loaded, see #326) — skip until the entity is available.
        entity_state = self.hass.states.get(weather_entity)
        if entity_state is None or entity_state.state in ("unavailable", "unknown"):
            _LOGGER.debug(
                "Weather entity %s not available, skipping forecast read",
                weather_entity,
            )
            self._outdoor_forecast = []
            return []

        # Modern approach: use weather.get_forecasts service (HA 2024.6+)
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
            entity_data = response.get(weather_entity, {}) if isinstance(response, dict) else {}  # type: ignore[union-attr]
            forecasts = entity_data.get("forecast", []) if isinstance(entity_data, dict) else []
            if isinstance(forecasts, list) and forecasts:
                result = self._convert_forecast_temps(forecasts)  # type: ignore[arg-type]
                self._outdoor_forecast = result
                return result
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "weather.get_forecasts service call failed for %s, falling back to state attributes",
                weather_entity,
            )

        # Fallback: read deprecated state attribute (older HA versions)
        state = self.hass.states.get(weather_entity)
        if state is None:
            self._outdoor_forecast = []
            return []
        forecast = state.attributes.get("forecast")
        if isinstance(forecast, list):
            result = self._convert_forecast_temps(forecast)
            self._outdoor_forecast = result
            return result
        self._outdoor_forecast = []
        return []

    def _convert_forecast_temps(self, forecasts: list[dict]) -> list[dict]:
        """Convert forecast temperatures from HA units to Celsius."""
        result = []
        for f in forecasts:
            if "temperature" in f:
                result.append({**f, "temperature": ha_temp_to_celsius(self.hass, f["temperature"])})
            else:
                result.append(f)
        return result

    @staticmethod
    def extract_cloud_series(forecast: list[dict]) -> list[float | None] | None:
        """Extract cloud_coverage values from forecast entries.

        Returns None if no cloud data is available (clear-sky fallback).
        """
        if not forecast:
            return None
        series: list[float | None] = []
        for entry in forecast:
            cc = entry.get("cloud_coverage")
            series.append(float(cc) if cc is not None else None)
        return series if any(v is not None for v in series) else None
