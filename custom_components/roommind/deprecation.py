"""Deprecation of the per-room entities replaced by the room climate entity (#238)."""

from __future__ import annotations

import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .coordinator import _match_room_entity

_LOGGER = logging.getLogger(__name__)

DEPRECATED_ENTITIES_ISSUE = "deprecated_entities"
# Per-room unique_id suffixes the room climate entity (`_climate`) replaces. The climate control
# switch stays: its off lets go of the devices, the room entity's off switches them off (#481).
DEPRECATED_ROOM_SUFFIXES = ("_target_temp", "_mode", "_override")

_warned: set[str] = set()


def warn_deprecated_use(entity_id: str, area_id: str) -> None:
    """Log once per entity that something still drives a deprecated entity."""
    if entity_id in _warned:
        return
    _warned.add(entity_id)
    _LOGGER.warning(
        "%s is deprecated and will be removed in a future release, use climate.%s_%s instead",
        entity_id,
        DOMAIN,
        area_id,
    )


def _deprecated_area(unique_id: str, rooms: dict) -> str | None:
    """Return the area of a deprecated per-room entity, None for any other uid."""
    match = _match_room_entity(unique_id.removeprefix(f"{DOMAIN}_"), rooms)
    if match is None or match[1] not in DEPRECATED_ROOM_SUFFIXES:
        return None
    return match[0]


@callback
def async_check_deprecated_entities(hass: HomeAssistant) -> None:
    """Raise a repair issue while automations or scripts still use a deprecated entity.

    Only used entities count, so users who never touched them are not bothered.
    """
    from homeassistant.components.automation import automations_with_entity
    from homeassistant.components.script import scripts_with_entity

    store = hass.data.get(DOMAIN, {}).get("store")
    if store is None:
        return
    rooms = store.get_rooms()
    registry = er.async_get(hass)
    lines: list[str] = []
    for entry in list(registry.entities.values()):
        if entry.platform != DOMAIN or entry.disabled or not isinstance(entry.unique_id, str):
            continue
        area_id = _deprecated_area(entry.unique_id, rooms)
        if area_id is None:
            continue
        users = automations_with_entity(hass, entry.entity_id) + scripts_with_entity(hass, entry.entity_id)
        if not users:
            continue
        replacement = (
            registry.async_get_entity_id("climate", DOMAIN, f"{DOMAIN}_{area_id}_climate")
            or f"climate.{DOMAIN}_{area_id}"
        )
        lines.append(f"- `{entry.entity_id}` → `{replacement}` ({', '.join(sorted(users))})")

    if not lines:
        ir.async_delete_issue(hass, DOMAIN, DEPRECATED_ENTITIES_ISSUE)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        DEPRECATED_ENTITIES_ISSUE,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=DEPRECATED_ENTITIES_ISSUE,
        translation_placeholders={"entities": "\n".join(sorted(lines))},
        learn_more_url="https://github.com/snazzybean/roommind#room-climate-entity",
    )


@callback
def async_schedule_deprecation_check(hass: HomeAssistant) -> None:
    """Check once automations and scripts are loaded: now on a reload, after startup otherwise."""
    if hass.state is CoreState.running:
        async_check_deprecated_entities(hass)
        return

    @callback
    def _on_started(_event: Event) -> None:
        async_check_deprecated_entities(hass)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)
