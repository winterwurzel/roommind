"""The global boiler heating switch must survive orphaned-entity cleanup.

Its unique_id matches no room, so without an entry in the global allowlist the
startup cleanup deletes it right after the switch platform creates it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.roommind.const import DOMAIN

from .conftest import _create_coordinator


def test_cleanup_keeps_boiler_heating_switch(hass, mock_config_entry):
    coordinator = _create_coordinator(hass, mock_config_entry)
    store = MagicMock()
    store.get_rooms.return_value = {"living_room": {}}
    hass.data = {DOMAIN: {"store": store}}

    boiler = MagicMock()
    boiler.unique_id = f"{DOMAIN}_boiler_heating"
    boiler.entity_id = "switch.roommind_boiler_heating"
    registry = MagicMock()
    registry.entities.values.return_value = [boiler]

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=registry):
        coordinator.cleanup_orphaned_entities()

    registry.async_remove.assert_not_called()
