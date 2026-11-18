"""Presence detection utilities for RoomMind."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

from ..const import DEFAULT_OCCUPANCY_ECO_DELAY_MINUTES

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def is_presence_away(hass: HomeAssistant, room: dict, settings: dict) -> bool:
    """Return True if presence detection says all relevant persons are away.

    Per-room persons take precedence over global persons.
    Fail-safe: unavailable/unknown entities are treated as "home".
    Entities that no longer exist are skipped (see _is_entity_gone).
    """
    if not settings.get("presence_enabled", False):
        return False
    global_persons = settings.get("presence_persons", [])
    if not global_persons:
        return False

    room_persons = room.get("presence_persons", [])
    persons = room_persons if room_persons else global_persons

    evaluated = 0
    for pid in persons:
        state = hass.states.get(pid)
        if state is None:
            # A deleted entity used to disable presence for the whole room
            # forever (#397): it can no longer be unassigned once it is gone
            # from the global list, and the fail-safe below then pinned the
            # room to "home". Skip it and let the remaining entities decide.
            if _is_entity_gone(hass, pid):
                continue
            return False  # fail-safe: entity exists but has no state yet
        if state.state in ("unavailable", "unknown"):
            return False  # fail-safe: treat as home
        evaluated += 1
        if _is_entity_home(state):
            return False
    return evaluated > 0


def _is_entity_gone(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True if *entity_id* has no state because it is gone for good.

    A live entity always has a state, so a missing one means the entity is
    deleted, disabled, or has not been added yet. Only the last case must stay
    fail-safe, and it is the one bounded by startup: until HA has started,
    platforms are still writing their initial states. Registration alone cannot
    make that call — entities without a unique_id (YAML template sensors,
    legacy device trackers) never get a registry entry even while alive.
    """
    if not hass.is_running:
        return False
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return True
    return entry.disabled_by is not None


def _is_entity_home(state: State) -> bool:
    """Check if a presence entity indicates someone is home.

    person.*/device_tracker.* use "home"/"not_home"; binary_sensor/input_boolean use "on"/"off".
    """
    if state.entity_id.startswith(("person.", "device_tracker.")):
        return bool(state.state == "home")
    return bool(state.state == "on")


def is_room_vacant(hass: HomeAssistant, room: dict, now: float) -> bool:
    """Return True once every occupancy sensor of an occupancy-eco room has been off for the delay (#180).

    Reads ``last_changed`` instead of timing the poll, so a motion pulse shorter
    than the update interval still restarts the delay. Unavailable, unknown or
    missing sensors count as occupied.
    """
    sensors = room.get("occupancy_sensors", [])
    if not room.get("occupancy_eco_enabled", False) or not sensors:
        return False
    last_change = 0.0
    for eid in sensors:
        state = hass.states.get(eid)
        if state is None or state.state != "off":
            return False
        last_change = max(last_change, state.last_changed.timestamp())
    delay_minutes = float(room.get("occupancy_eco_delay", DEFAULT_OCCUPANCY_ECO_DELAY_MINUTES))
    return now - last_change >= delay_minutes * 60
