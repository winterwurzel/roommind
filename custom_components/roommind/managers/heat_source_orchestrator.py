"""Heat source orchestrator for rooms with multiple heating device types.

When a room has both thermostats (e.g. radiator TRVs connected to a gas boiler)
and ACs with heating capability (heat pumps), this module decides which devices
to activate based on temperature gap (delta-T) and outdoor temperature.

Roles are fixed: thermostats are always primary, ACs are always secondary.

The orchestrator sits between the MPC optimizer output (abstract mode + power_fraction)
and the device command layer (async_apply). It does NOT modify the thermal model or
optimizer. It only filters and distributes the power decision across devices.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_HEAT_SOURCE_AC_MIN_OUTDOOR,
    DEFAULT_HEAT_SOURCE_OUTDOOR_THRESHOLD,
    DEFAULT_HEAT_SOURCE_PRIMARY_DELTA,
    HEAT_SOURCE_HYSTERESIS,
    HEAT_SOURCE_LARGE_GAP_MULTIPLIER,
    HEAT_SOURCE_SECONDARY_POWER_SCALE,
    MODE_HEATING,
)
from ..utils.device_utils import get_ac_eids, get_electric_eids, get_trv_eids, has_reliable_hvac_modes

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceCommand:
    """Command for a single climate device within a heat source plan."""

    entity_id: str
    role: str  # "primary" | "secondary"
    device_type: str  # "thermostat" | "ac"
    active: bool
    power_fraction: float  # 0.0-1.0
    reason: str
    # Per-device heat target overriding the room target. Set when the boiler is
    # disabled and a TRV only runs as the cold-room fallback, so it heats to the
    # floor temperature rather than to comfort.
    target_temp: float | None = None


@dataclass
class HeatSourcePlan:
    """Orchestration plan describing which heating devices to activate."""

    commands: list[DeviceCommand]
    active_sources: str  # "primary" | "secondary" | "both" | "none"
    reason: str


def _is_available(hass: HomeAssistant, entity_id: str) -> bool:
    """Check if an entity is available (not unavailable/unknown)."""
    state = hass.states.get(entity_id)
    if state is None:
        return False
    return state.state not in ("unavailable", "unknown")


def _ac_can_heat(hass: HomeAssistant, entity_id: str) -> bool:
    """Check if a single AC entity supports heating and is available."""
    state = hass.states.get(entity_id)
    if state is None:
        return False
    if state.state in ("unavailable", "unknown"):
        return False
    modes = state.attributes.get("hvac_modes", [])
    if "heat" in modes or "heat_cool" in modes or "auto" in modes:
        return True
    # Modes unreliable (no active modes in list) — assume it can heat.
    return not has_reliable_hvac_modes(state)


def evaluate_heat_sources(
    room_config: dict,
    mode: str,
    power_fraction: float,
    current_temp: float | None,
    target_temp: float | None,
    outdoor_temp: float | None,
    previous_active_sources: str,
    hass: HomeAssistant,
    boiler_enabled: bool = True,
    boiler_floor_temp: float | None = None,
) -> HeatSourcePlan | None:
    """Evaluate which heating devices to activate.

    With ``boiler_enabled`` False the decision is delegated to
    evaluate_boiler_disabled; see there.

    Returns a HeatSourcePlan for MODE_HEATING, or None if orchestration
    should not apply (wrong mode, disabled, or missing data).
    """
    if mode != MODE_HEATING:
        return None

    if not boiler_enabled:
        return evaluate_boiler_disabled(
            room_config=room_config,
            power_fraction=power_fraction,
            current_temp=current_temp,
            target_temp=target_temp,
            outdoor_temp=outdoor_temp,
            previous_active_sources=previous_active_sources,
            hass=hass,
            floor_temp=boiler_floor_temp,
        )

    if not room_config.get("heat_source_orchestration", False):
        return None

    thermostats = get_trv_eids(room_config.get("devices", []))
    acs = get_ac_eids(room_config.get("devices", []))
    electrics = get_electric_eids(room_config.get("devices", []))
    # Orchestration needs a boiler-driven source and at least one electric
    # alternative to choose between.
    if not thermostats or not (acs or electrics):
        return None

    if current_temp is None or target_temp is None:
        return None

    primary_delta = room_config.get("heat_source_primary_delta", DEFAULT_HEAT_SOURCE_PRIMARY_DELTA)
    outdoor_threshold = room_config.get("heat_source_outdoor_threshold", DEFAULT_HEAT_SOURCE_OUTDOOR_THRESHOLD)
    ac_min_outdoor = room_config.get("heat_source_ac_min_outdoor", DEFAULT_HEAT_SOURCE_AC_MIN_OUTDOOR)

    delta_t = target_temp - current_temp

    # Fixed roles: boiler-driven thermostats = primary, electric sources = secondary.
    #
    # Resistive electric heaters only participate while prefer_electric_heat is on.
    # A heat pump can beat a boiler on running cost in mild weather, but resistive
    # heat never does - it is worth running only when the power is surplus that
    # would otherwise be exported, so it must not be picked on cost heuristics.
    prefer_electric_flag = bool(room_config.get("prefer_electric_heat", False))
    primary_devices: list[tuple[str, str]] = [(eid, "thermostat") for eid in thermostats]
    secondary_devices: list[tuple[str, str]] = [(eid, "ac") for eid in acs]
    if prefer_electric_flag:
        secondary_devices += [(eid, "electric") for eid in electrics]

    # Electric heaters kept out of the selectable group still need explicit idle
    # commands, otherwise nothing turns them off once a surplus window ends.
    unselected_electrics = [] if prefer_electric_flag else list(electrics)

    def _idle_electric_cmds() -> list[DeviceCommand]:
        return [
            DeviceCommand(
                entity_id=eid,
                role="secondary",
                device_type="electric",
                active=False,
                power_fraction=0.0,
                reason="electric heat not preferred",
            )
            for eid in unselected_electrics
        ]

    # Early exit: at or above target, no heating needed
    if delta_t <= 0:
        idle_cmds: list[DeviceCommand] = _idle_electric_cmds()
        for eid, device_type in primary_devices:
            idle_cmds.append(
                DeviceCommand(
                    entity_id=eid,
                    role="primary",
                    device_type=device_type,
                    active=False,
                    power_fraction=0.0,
                    reason="not selected",
                )
            )
        for eid, device_type in secondary_devices:
            idle_cmds.append(
                DeviceCommand(
                    entity_id=eid,
                    role="secondary",
                    device_type=device_type,
                    active=False,
                    power_fraction=0.0,
                    reason="not selected",
                )
            )
        _LOGGER.debug(
            "Room '%s': heat source orchestration → none (delta_t=%.1f, outdoor=%s)",
            room_config.get("area_id", "?"),
            delta_t,
            outdoor_temp,
        )
        return HeatSourcePlan(commands=idle_cmds, active_sources="none", reason="delta_t <= 0")

    # Hardware protection: disable AC heating in extreme cold
    ac_disabled = outdoor_temp is not None and outdoor_temp < ac_min_outdoor
    if ac_disabled:
        # Remove ACs from both lists, they cannot heat
        primary_devices = [(eid, dt) for eid, dt in primary_devices if dt != "ac"]
        secondary_devices = [(eid, dt) for eid, dt in secondary_devices if dt != "ac"]

    # Filter ACs that don't support heating or are unavailable
    primary_devices = [(eid, dt) for eid, dt in primary_devices if dt != "ac" or _ac_can_heat(hass, eid)]
    secondary_devices = [(eid, dt) for eid, dt in secondary_devices if dt != "ac" or _ac_can_heat(hass, eid)]

    # Filter unavailable thermostats and electric heaters. Electric heaters skip the
    # AC filters above on purpose: no compressor, so no cold-weather capability limit
    # and no hvac_modes capability probe.
    primary_devices = [(eid, dt) for eid, dt in primary_devices if dt == "ac" or _is_available(hass, eid)]
    secondary_devices = [(eid, dt) for eid, dt in secondary_devices if dt == "ac" or _is_available(hass, eid)]

    # Determine which source group to activate
    large_gap_threshold = primary_delta * HEAT_SOURCE_LARGE_GAP_MULTIPLIER

    # Electric preference (e.g. heating from surplus PV): electric sources must win
    # over the boiler regardless of outdoor temperature, and a large gap must not
    # pull the boiler in as well - the point is that the boiler runs less. Requires
    # a usable electric source: the filtering above drops ACs in extreme cold or
    # without heat support and drops unavailable heaters, and in that case normal
    # selection applies so the room is not left unheated.
    prefer_electric = prefer_electric_flag and bool(secondary_devices)

    # Weather-based preference with hysteresis (None when no outdoor data available)
    prefer_ac: bool | None
    if prefer_electric:
        prefer_ac = True
    elif outdoor_temp is not None:
        if previous_active_sources == "secondary":
            # AC was active: keep unless outdoor drops below threshold - hysteresis
            prefer_ac = outdoor_temp > outdoor_threshold - HEAT_SOURCE_HYSTERESIS
        elif previous_active_sources == "primary":
            # Boiler was active: keep unless outdoor rises above threshold + hysteresis
            prefer_ac = outdoor_temp > outdoor_threshold + HEAT_SOURCE_HYSTERESIS
        else:
            prefer_ac = outdoor_temp > outdoor_threshold
    else:
        prefer_ac = None

    # "both" when gap is large, or hysteresis holds "both" state
    if prefer_electric:
        active = "secondary"
    elif delta_t >= large_gap_threshold + HEAT_SOURCE_HYSTERESIS:
        active = "both"
    elif previous_active_sources == "both" and delta_t > primary_delta - HEAT_SOURCE_HYSTERESIS:
        active = "both"
    elif prefer_ac is True:
        active = "secondary"
    elif prefer_ac is False:
        active = "primary"
    else:
        # No outdoor data: delta-T heuristic (backward compatible)
        active = "primary" if delta_t >= primary_delta + HEAT_SOURCE_HYSTERESIS else "secondary"

    # Edge case: if chosen group has no devices, fall back
    if active == "secondary" and not secondary_devices:
        active = "primary"
    if active == "primary" and not primary_devices:
        active = "secondary"
    if active == "both" and not primary_devices:
        active = "secondary"
    if active == "both" and not secondary_devices:
        active = "primary"
    if not primary_devices and not secondary_devices:
        active = "none"

    # Build device commands
    commands: list[DeviceCommand] = []
    reason_parts: list[str] = []

    if ac_disabled and active != "none":
        reason_parts.append(f"AC disabled (outdoor {outdoor_temp}°C < {ac_min_outdoor}°C)")

    if active == "both":
        reason_parts.append(f"large gap ({delta_t:.1f}°C)")
    elif active == "primary":
        outdoor_str = f"{outdoor_temp}°C" if outdoor_temp is not None else "n/a"
        reason_parts.append(f"boiler preferred ({delta_t:.1f}°C gap, outdoor {outdoor_str})")
    elif active == "secondary":
        outdoor_str = f"{outdoor_temp}°C" if outdoor_temp is not None else "n/a"
        if prefer_electric:
            reason_parts.append(f"electric preferred ({delta_t:.1f}°C gap, outdoor {outdoor_str})")
        else:
            reason_parts.append(f"AC preferred ({delta_t:.1f}°C gap, outdoor {outdoor_str})")

    for eid, device_type in primary_devices:
        is_active = active in ("primary", "both")
        pf = power_fraction if is_active else 0.0
        commands.append(
            DeviceCommand(
                entity_id=eid,
                role="primary",
                device_type=device_type,
                active=is_active,
                power_fraction=pf,
                reason="active" if is_active else "not selected",
            )
        )

    for eid, device_type in secondary_devices:
        is_active = active in ("secondary", "both")
        pf = power_fraction
        if active == "both":
            pf = round(power_fraction * HEAT_SOURCE_SECONDARY_POWER_SCALE, 2)
        if not is_active:
            pf = 0.0
        commands.append(
            DeviceCommand(
                entity_id=eid,
                role="secondary",
                device_type=device_type,
                active=is_active,
                power_fraction=pf,
                reason="active" if is_active else "not selected",
            )
        )

    commands.extend(_idle_electric_cmds())

    reason = "; ".join(reason_parts) if reason_parts else active

    _LOGGER.debug(
        "Room '%s': heat source orchestration → %s (delta_t=%.1f, outdoor=%s)",
        room_config.get("area_id", "?"),
        active,
        delta_t,
        outdoor_temp,
    )

    return HeatSourcePlan(commands=commands, active_sources=active, reason=reason)


def _usable_electric_sources(
    hass: HomeAssistant,
    room_config: dict,
    outdoor_temp: float | None,
) -> list[tuple[str, str]]:
    """ACs and electric heaters that can heat right now.

    Same filters as the normal path: ACs drop out in extreme cold or without heat
    support, electric heaters only when unavailable.
    """
    devices = room_config.get("devices", [])
    ac_min_outdoor = room_config.get("heat_source_ac_min_outdoor", DEFAULT_HEAT_SOURCE_AC_MIN_OUTDOOR)
    ac_disabled = outdoor_temp is not None and outdoor_temp < ac_min_outdoor
    sources: list[tuple[str, str]] = []
    if not ac_disabled:
        sources += [(eid, "ac") for eid in get_ac_eids(devices) if _ac_can_heat(hass, eid)]
    sources += [(eid, "electric") for eid in get_electric_eids(devices) if _is_available(hass, eid)]
    return sources


def _fallback_active(
    current_temp: float,
    floor_temp: float | None,
    previous_active_sources: str,
) -> bool:
    """Whether the boiler may run as the cold-room fallback.

    Starts below the floor and, once running, holds until the room is
    HEAT_SOURCE_HYSTERESIS above it so the TRVs do not chatter at the threshold.
    """
    if floor_temp is None:
        return False
    if current_temp < floor_temp:
        return True
    was_running = previous_active_sources in ("primary", "both")
    return was_running and current_temp < floor_temp + HEAT_SOURCE_HYSTERESIS


def evaluate_boiler_disabled(
    room_config: dict,
    *,
    power_fraction: float,
    current_temp: float | None,
    target_temp: float | None,
    outdoor_temp: float | None,
    previous_active_sources: str,
    hass: HomeAssistant,
    floor_temp: float | None,
) -> HeatSourcePlan | None:
    """Plan heating while the boiler is switched off globally.

    Electric sources (ACs and resistive heaters) run only while the room's
    prefer_electric_heat flag is on - that flag is what the surplus automations
    drive, so without surplus they stay off. Boiler-fed TRVs run only as a
    fallback when the room falls below ``floor_temp``, and then heat to the floor,
    not to the room target. Unlike the normal path this also applies to rooms
    without any electric source, so their TRVs are idled explicitly.

    Returns None only for rooms without TRVs, which the boiler cannot affect.
    """
    devices = room_config.get("devices", [])
    thermostats = get_trv_eids(devices)
    if not thermostats:
        return None

    if current_temp is None or target_temp is None:
        return _boiler_disabled_plan(
            room_config,
            thermostats,
            electric_sources=[],
            boiler_on=False,
            electric_on=False,
            power_fraction=0.0,
            primary_target=None,
            reason="no temperature data",
        )

    delta_t = target_temp - current_temp
    electric_sources: list[tuple[str, str]] = []
    if room_config.get("prefer_electric_heat", False):
        electric_sources = _usable_electric_sources(hass, room_config, outdoor_temp)
    electric_on = delta_t > 0 and bool(electric_sources)

    primary_target = min(floor_temp, target_temp) if floor_temp is not None else None
    boiler_on = delta_t > 0 and _fallback_active(current_temp, primary_target, previous_active_sources)

    reason = f"boiler disabled ({delta_t:.1f}°C gap)"
    if boiler_on:
        reason += f"; below floor {primary_target}°C"
    return _boiler_disabled_plan(
        room_config,
        thermostats,
        electric_sources=electric_sources,
        boiler_on=boiler_on,
        electric_on=electric_on,
        power_fraction=power_fraction,
        primary_target=primary_target,
        reason=reason,
    )


def _boiler_disabled_plan(
    room_config: dict,
    thermostats: list[str],
    *,
    electric_sources: list[tuple[str, str]],
    boiler_on: bool,
    electric_on: bool,
    power_fraction: float,
    primary_target: float | None,
    reason: str,
) -> HeatSourcePlan:
    """Build commands for every heating device in the room."""
    commands = [
        DeviceCommand(
            entity_id=eid,
            role="primary",
            device_type="thermostat",
            active=boiler_on,
            power_fraction=power_fraction if boiler_on else 0.0,
            reason="below floor" if boiler_on else "boiler disabled",
            target_temp=primary_target if boiler_on else None,
        )
        for eid in thermostats
    ]
    selected = {eid for eid, _ in electric_sources}
    devices = room_config.get("devices", [])
    secondaries = [(eid, "ac") for eid in get_ac_eids(devices)]
    secondaries += [(eid, "electric") for eid in get_electric_eids(devices)]
    for eid, device_type in secondaries:
        is_active = electric_on and eid in selected
        commands.append(
            DeviceCommand(
                entity_id=eid,
                role="secondary",
                device_type=device_type,
                active=is_active,
                power_fraction=power_fraction if is_active else 0.0,
                reason="electric preferred" if is_active else "electric heat not preferred",
            )
        )

    if boiler_on and electric_on:
        active = "both"
    elif boiler_on:
        active = "primary"
    elif electric_on:
        active = "secondary"
    else:
        active = "none"

    _LOGGER.debug("Room '%s': boiler disabled → %s (%s)", room_config.get("area_id", "?"), active, reason)
    return HeatSourcePlan(commands=commands, active_sources=active, reason=reason)
