"""Short, explicitly armed relative-map waypoint steering *experiment*.

This is NOT autonomous navigation. There is no collision, terrain or hazard
sensor and normalized zone-map coordinates are not world coordinates. A human
must choose a nearby point in an unobstructed area and approve each run.

The controller probes heading with one short forward movement, then makes
bounded left/right adjustments followed by measured forward movements. It
stops on stale/bad telemetry, combat, focus loss, map change, no progress,
contradictory turn observations, time limits, or action budgets.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import atan2, degrees, hypot, isfinite
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import (
    KeyboardDriver, LiveControlError, MovementProbeResult,
    _validate_telemetry, probe_movement,
)
from wotlkbuddy.live.heading import apparent_bearing, shortest_angle_difference


@dataclass(frozen=True, slots=True)
class WaypointReport:
    character: str
    zone: str
    start_map_x: float
    start_map_y: float
    target_map_x: float
    target_map_y: float
    end_map_x: float
    end_map_y: float
    initial_distance: float
    final_distance: float
    forward_pulses: int
    turn_pulses: int
    total_key_down_seconds: float
    stopped_reason: str
    within_tolerance: bool
    samples: tuple[MovementProbeResult, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def guarded_waypoint(
    *,
    offset_x: float,
    offset_y: float,
    capture: Callable[[], ClientTelemetry],
    keyboard: KeyboardDriver,
    forward_duration: float = 0.12,
    tolerance: float = 0.00013,
    turn_rate_deg_s: float = 180.0,
    max_forward_pulses: int = 6,
    max_turn_pulses: int = 4,
    max_map_distance: float = 0.002,
    max_elapsed_seconds: float = 30.0,
    stop_requested: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
) -> WaypointReport:
    """Try to approach a nearby map-relative offset with bounded feedback.

    The first pulse measures travel direction; no initial facing/yaw is assumed.
    A successful report means only that map coordinates fell in tolerance, not
    that physical navigation, collision safety, or quest travel was verified.
    """
    if not all(isfinite(v) for v in (
        offset_x, offset_y, forward_duration, tolerance, turn_rate_deg_s,
        max_map_distance, max_elapsed_seconds,
    )):
        raise ValueError('All waypoint parameters must be finite numbers')
    target_offset = hypot(offset_x, offset_y)
    if not 0.00035 <= target_offset <= 0.0015:
        raise ValueError('Relative target must be 0.00035..0.0015 map units away')
    if not 0.08 <= forward_duration <= 0.15:
        raise ValueError('forward_duration must be 0.08..0.15 seconds')
    if not 0.00007 <= tolerance <= 0.0002 or tolerance >= target_offset / 2:
        raise ValueError('tolerance must be 0.00007..0.0002 and under half target distance')
    if not 100.0 <= turn_rate_deg_s <= 300.0:
        raise ValueError('turn_rate_deg_s must be 100..300 degrees per second')
    if not isinstance(max_forward_pulses, int) or not 2 <= max_forward_pulses <= 6:
        raise ValueError('max_forward_pulses must be 2..6')
    if not isinstance(max_turn_pulses, int) or not 0 <= max_turn_pulses <= 4:
        raise ValueError('max_turn_pulses must be 0..4')
    if not 0.001 <= max_map_distance <= 0.003:
        raise ValueError('max_map_distance must be 0.001..0.003')
    if not 10.0 <= max_elapsed_seconds <= 45.0:
        raise ValueError('max_elapsed_seconds must be 10..45')
    if max_forward_pulses * forward_duration + max_turn_pulses * 0.18 > 1.5:
        raise ValueError('Cumulative key-down budget cannot exceed 1.5 seconds')

    stopped = stop_requested or (lambda: False)
    started = now()
    if stopped():
        raise LiveControlError('Emergency stop requested (Escape)')
    if not keyboard.wow_is_foreground():
        raise LiveControlError('WoW must be the active window')
    baseline = capture()
    _validate_telemetry(baseline)
    origin = baseline.player
    target_x, target_y = origin.map_x + offset_x, origin.map_y + offset_y
    if not (0 < target_x < 1 and 0 < target_y < 1):
        raise LiveControlError('Relative waypoint would be outside current zone map')

    samples: list[MovementProbeResult] = []
    x, y = origin.map_x, origin.map_y
    distance = target_offset
    total_key_time = 0.0
    total_map_distance = 0.0
    forward_count = turn_count = diverging_count = 0
    estimated_rate = turn_rate_deg_s
    bearing: float | None = None
    pending_turn: tuple[str, float, float] | None = None
    reason = 'forward_limit'

    def guarded_pause(seconds: float) -> None:
        remaining = seconds
        while remaining > 1e-9:
            if stopped():
                raise LiveControlError('Emergency stop requested (Escape)')
            if not keyboard.wow_is_foreground():
                raise LiveControlError('WoW lost focus during waypoint experiment')
            if now() - started >= max_elapsed_seconds:
                raise LiveControlError('Waypoint elapsed-time limit reached')
            chunk = min(0.03, remaining)
            sleep(chunk)
            remaining -= chunk

    def pulse(key: str, duration: float) -> MovementProbeResult:
        nonlocal x, y, total_key_time, total_map_distance
        if stopped():
            raise LiveControlError('Emergency stop requested (Escape)')
        if now() - started >= max_elapsed_seconds:
            raise LiveControlError('Waypoint elapsed-time limit reached')
        if total_key_time + duration > 1.5 + 1e-9:
            raise LiveControlError('Cumulative key-down budget reached')
        if not keyboard.wow_is_foreground():
            raise LiveControlError('WoW lost focus before movement')
        current = capture()
        _validate_telemetry(current)
        p = current.player
        if (p.guid != origin.guid or p.name != origin.name or
                p.continent != origin.continent or p.map_zone != origin.map_zone or
                p.zone_name != origin.zone_name):
            raise LiveControlError('Character or map changed during waypoint experiment')
        if hypot(p.map_x - x, p.map_y - y) > 0.00012:
            raise LiveControlError('Uncommanded movement detected between pulses')
        event = probe_movement(key=key, duration=duration, capture=capture,
                               keyboard=keyboard, sleep=guarded_pause)
        if event.character != origin.name or event.zone != origin.zone_name:
            raise LiveControlError('Character or zone changed after movement')
        samples.append(event)
        total_key_time += duration
        total_map_distance += hypot(event.dx, event.dy)
        x, y = event.after_map_x, event.after_map_y
        return event

    for _ in range(max_forward_pulses):
        if stopped():
            reason = 'user_stop'
            break
        if now() - started >= max_elapsed_seconds:
            reason = 'time_limit'
            break
        if total_map_distance >= max_map_distance:
            reason = 'distance_budget'
            break
        prior_distance = distance
        # A full-length step can overshoot a nearby waypoint while heading is
        # still being corrected. Keep a nonzero probe for valid feedback,
        # but taper it as the remaining map distance decreases.
        step_duration = min(forward_duration, max(0.08, forward_duration * distance / 0.0005))
        move = pulse('forward', step_duration)
        forward_count += 1
        step = hypot(move.dx, move.dy)
        if step < 0.00003:
            reason = 'stalled'
            break
        bearing = apparent_bearing(move.dx, move.dy, minimum_displacement=0.00003)
        if pending_turn is not None:
            turn_key, turn_duration, before_bearing = pending_turn
            actual_turn = shortest_angle_difference(before_bearing, bearing)
            if (abs(actual_turn) < 3 or
                    (actual_turn > 0) != (turn_key == 'turn-right') or
                    abs(actual_turn) > 80):
                reason = 'steering_unreliable'
                break
            measured_rate = abs(actual_turn) / turn_duration
            if not 50 <= measured_rate <= 400:
                reason = 'steering_unreliable'
                break
            # Limit influence of any single measurement (map axes are distorted).
            estimated_rate = min(300.0, max(100.0,
                0.6 * estimated_rate + 0.4 * measured_rate))
            pending_turn = None

        distance = hypot(target_x - x, target_y - y)
        if distance <= tolerance:
            reason = 'within_tolerance'
            break
        if total_map_distance >= max_map_distance:
            reason = 'distance_budget'
            break
        if distance >= prior_distance - 0.000008:
            diverging_count += 1
        else:
            diverging_count = 0
        if diverging_count >= 2:
            reason = 'not_converging'
            break
        desired_bearing = degrees(atan2(target_y - y, target_x - x))
        delta = shortest_angle_difference(bearing, desired_bearing)
        if abs(delta) > 80:
            reason = 'heading_out_of_range'
            break
        if abs(delta) <= 12:
            continue
        # A turn without a subsequent forward sample cannot be verified.
        if forward_count >= max_forward_pulses:
            reason = 'forward_limit'
            break
        if turn_count >= max_turn_pulses:
            reason = 'turn_limit'
            break
        duration = min(0.18, max(0.05, abs(delta) / estimated_rate * 0.7))
        turn_key = 'turn-right' if delta > 0 else 'turn-left'
        turn = pulse(turn_key, duration)
        turn_count += 1
        if hypot(turn.dx, turn.dy) > 0.0001:
            reason = 'turn_drift'
            break
        pending_turn = (turn_key, duration, bearing)
    return WaypointReport(
        character=origin.name, zone=origin.zone_name,
        start_map_x=origin.map_x, start_map_y=origin.map_y,
        target_map_x=target_x, target_map_y=target_y,
        end_map_x=x, end_map_y=y, initial_distance=target_offset,
        final_distance=hypot(target_x - x, target_y - y),
        forward_pulses=forward_count, turn_pulses=turn_count,
        total_key_down_seconds=total_key_time, stopped_reason=reason,
        within_tolerance=(reason == 'within_tolerance'), samples=tuple(samples),
    )
