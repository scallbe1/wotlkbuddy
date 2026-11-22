"""Human-armed two-leg route experiment built from validated short waypoints.

Not terrain-aware navigation: each leg is a very short relative map offset,
chosen by the operator after verifying clear, open ground. Stop on the first
unsuccessful leg. No QuestBot or unattended execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot, isfinite
from time import monotonic, sleep as real_sleep
from typing import Callable, Sequence

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import KeyboardDriver, LiveControlError, _validate_telemetry
from wotlkbuddy.live.waypoint import WaypointReport, guarded_waypoint


@dataclass(frozen=True, slots=True)
class RouteReport:
    character: str
    zone: str
    legs_requested: int
    legs_completed: int
    start_map_x: float
    start_map_y: float
    end_map_x: float
    end_map_y: float
    forward_pulses: int
    turn_pulses: int
    total_key_down_seconds: float
    stopped_reason: str
    legs: tuple[WaypointReport, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def validate_legs(legs: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    """Reject unsafe or malformed route plans before inspecting or moving WoW."""
    if not 1 <= len(legs) <= 2:
        raise ValueError('Route must have one or two short relative legs')
    parsed: list[tuple[float, float]] = []
    total = 0.0
    for i, leg in enumerate(legs, 1):
        if len(leg) != 2 or not all(isinstance(v, (int, float)) and isfinite(v) for v in leg):
            raise ValueError(f'Route leg {i} requires two finite numeric offsets')
        x, y = float(leg[0]), float(leg[1])
        length = hypot(x, y)
        if not 0.00035 <= length <= 0.0009:
            raise ValueError(f'Route leg {i} length must be 0.00035..0.0009 map units')
        total += length
        parsed.append((x, y))
    if total > 0.0016:
        raise ValueError('Total planned route must be at most 0.0016 map units')
    return tuple(parsed)


def guarded_route(
    *,
    legs: Sequence[tuple[float, float]],
    capture: Callable[[], ClientTelemetry],
    keyboard: KeyboardDriver,
    forward_duration: float = 0.12,
    tolerance: float = 0.00013,
    turn_rate_deg_s: float = 180.4,
    stop_requested: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
) -> RouteReport:
    """Execute at most two short legs, stopping on any non-successful leg.

    Each leg revalidates WoW focus, player, combat, map and live feedback.
    Key-down budget is <= 2.1s total by construction (5 forward and 3 turn
    pulses per leg). A route is never automatically retried or rerouted.
    """
    offsets = validate_legs(legs)
    if not keyboard.wow_is_foreground():
        raise LiveControlError('WoW must be the active window')
    stop = stop_requested or (lambda: False)
    if stop():
        raise LiveControlError('Emergency stop requested (Escape)')
    baseline = capture()
    _validate_telemetry(baseline)
    origin = baseline.player
    # Reject crossing the edge of the zone map even before the first key.
    planned_x, planned_y = origin.map_x, origin.map_y
    for dx, dy in offsets:
        planned_x += dx
        planned_y += dy
        if not (0 < planned_x < 1 and 0 < planned_y < 1):
            raise LiveControlError('Route would leave the current zone map')
    expected_x, expected_y = origin.map_x, origin.map_y
    results: list[WaypointReport] = []
    started = now()
    reason = 'complete'
    for i, (dx, dy) in enumerate(offsets, 1):
        if stop():
            reason = 'user_stop'
            break
        if now() - started > 65:
            reason = 'time_limit'
            break
        if not keyboard.wow_is_foreground():
            raise LiveControlError('WoW focus lost before route leg')
        current = capture()
        _validate_telemetry(current)
        p = current.player
        if (p.guid != origin.guid or p.name != origin.name or
                p.continent != origin.continent or p.map_zone != origin.map_zone or
                p.zone_name != origin.zone_name):
            raise LiveControlError('Character or map changed between route legs')
        if hypot(p.map_x - expected_x, p.map_y - expected_y) > 0.00012:
            raise LiveControlError('Uncommanded movement between route legs')
        report = guarded_waypoint(
            offset_x=dx, offset_y=dy, capture=capture, keyboard=keyboard,
            forward_duration=forward_duration, tolerance=tolerance,
            turn_rate_deg_s=turn_rate_deg_s, max_forward_pulses=5,
            max_turn_pulses=3, stop_requested=stop, sleep=sleep, now=now,
        )
        results.append(report)
        expected_x, expected_y = report.end_map_x, report.end_map_y
        if not report.within_tolerance:
            reason = f'leg_{i}_{report.stopped_reason}'
            break
    end_x, end_y = expected_x, expected_y
    return RouteReport(
        character=origin.name, zone=origin.zone_name,
        legs_requested=len(offsets),
        legs_completed=sum(r.within_tolerance for r in results),
        start_map_x=origin.map_x, start_map_y=origin.map_y,
        end_map_x=end_x, end_map_y=end_y,
        forward_pulses=sum(r.forward_pulses for r in results),
        turn_pulses=sum(r.turn_pulses for r in results),
        total_key_down_seconds=sum(r.total_key_down_seconds for r in results),
        stopped_reason=reason, legs=tuple(results),
    )
