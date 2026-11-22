"""Explicitly armed, three-pulse heading experiment for WoW 3.3.5a.

This is NOT waypoint navigation. Measured angles are apparent 2D zone-map
angles, not player world yaw. Only a human-armed diagnostic may call it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import atan2, degrees, hypot
from time import sleep as real_sleep
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import (
    KeyboardDriver, LiveControlError, MovementProbeResult,
    _validate_telemetry, probe_movement,
)


@dataclass(frozen=True, slots=True)
class HeadingReport:
    character: str
    zone: str
    turn_key: str
    turn_duration: float
    forward_duration: float
    first_map_bearing_deg: float
    second_map_bearing_deg: float
    apparent_turn_deg: float
    apparent_turn_rate_deg_s: float
    first_map_step: float
    second_map_step: float
    turn_map_drift: float
    direction_matches_key: bool
    samples: tuple[MovementProbeResult, MovementProbeResult, MovementProbeResult]

    def to_dict(self) -> dict:
        return asdict(self)


def apparent_bearing(dx: float, dy: float, *, minimum_displacement: float) -> float:
    """Angle from map +X (right/east), positive toward +Y (down/south)."""
    if hypot(dx, dy) < minimum_displacement:
        raise LiveControlError('Forward movement too small to measure heading')
    return degrees(atan2(dy, dx))


def shortest_angle_difference(start: float, end: float) -> float:
    """Wrap an angle difference to [-180, 180), including seam crossings."""
    return (end - start + 180.0) % 360.0 - 180.0


def calibrate_heading(
    *,
    capture: Callable[[], ClientTelemetry],
    keyboard: KeyboardDriver,
    turn_key: str = 'turn-right',
    turn_duration: float = 0.18,
    forward_duration: float = 0.15,
    minimum_displacement: float = 0.00005,
    max_turn_drift: float = 0.00012,
    sleep: Callable[[float], None] = real_sleep,
    stop_requested: Callable[[], bool] | None = None,
) -> HeadingReport:
    """Briefly move, rotate, move again; compare observed displacement bearings.

    At most three pulses: W, A/D, W. Each attempts guaranteed key-up through
    probe_movement, with independent preflight and no automatic retries.
    """
    if turn_key not in {'turn-left', 'turn-right'}:
        raise ValueError('turn_key must be turn-left or turn-right')
    if not 0.05 <= turn_duration <= 0.20:
        raise ValueError('turn_duration must be 0.05..0.20 seconds')
    if not 0.05 <= forward_duration <= 0.20:
        raise ValueError('forward_duration must be 0.05..0.20 seconds')
    if 2 * forward_duration + turn_duration > 0.55:
        raise ValueError('total key-down duration exceeds 0.55 seconds')
    if not 0.00002 <= minimum_displacement <= 0.0002:
        raise ValueError('minimum_displacement must be 0.00002..0.0002')
    if not 0.0 < max_turn_drift <= 0.0002:
        raise ValueError('max_turn_drift must be 0..0.0002')

    stopped = stop_requested or (lambda: False)

    def guarded_pause(duration: float) -> None:
        remaining = duration
        while remaining > 1e-9:
            if stopped():
                raise LiveControlError('Emergency stop requested (Escape)')
            if not keyboard.wow_is_foreground():
                raise LiveControlError('WoW focus lost during heading experiment')
            chunk = min(0.03, remaining)
            sleep(chunk)
            remaining -= chunk

    if stopped():
        raise LiveControlError('Emergency stop requested (Escape)')
    if not keyboard.wow_is_foreground():
        raise LiveControlError('WoW must be the active foreground window')
    baseline = capture()
    _validate_telemetry(baseline)
    original = baseline.player
    last = (original.map_x, original.map_y)

    def pulse(key: str, duration: float) -> MovementProbeResult:
        nonlocal last
        if stopped():
            raise LiveControlError('Emergency stop requested (Escape)')
        if not keyboard.wow_is_foreground():
            raise LiveControlError('WoW focus lost during heading experiment')
        current = capture()
        _validate_telemetry(current)
        player = current.player
        if (player.guid != original.guid or player.name != original.name or
                player.continent != original.continent or
                player.map_zone != original.map_zone):
            raise LiveControlError('Character or map changed; heading test aborted')
        if hypot(player.map_x - last[0], player.map_y - last[1]) > max_turn_drift:
            raise LiveControlError('Uncommanded map displacement; heading test aborted')
        sample = probe_movement(
            key=key, duration=duration, capture=capture,
            keyboard=keyboard, sleep=guarded_pause,
        )
        if sample.character != original.name or sample.zone != original.zone_name:
            raise LiveControlError('Character or zone changed during heading test')
        if hypot(sample.before_map_x - last[0],
                 sample.before_map_y - last[1]) > max_turn_drift:
            raise LiveControlError('Uncommanded movement before pulse')
        last = (sample.after_map_x, sample.after_map_y)
        return sample

    first = pulse('forward', forward_duration)
    a0 = apparent_bearing(first.dx, first.dy, minimum_displacement=minimum_displacement)
    turn = pulse(turn_key, turn_duration)
    drift = hypot(turn.dx, turn.dy)
    if drift > max_turn_drift:
        raise LiveControlError('Position drifted during rotation; stopping before final pulse')
    second = pulse('forward', forward_duration)
    a1 = apparent_bearing(second.dx, second.dy, minimum_displacement=minimum_displacement)
    delta = shortest_angle_difference(a0, a1)
    return HeadingReport(
        character=original.name, zone=original.zone_name,
        turn_key=turn_key, turn_duration=turn_duration,
        forward_duration=forward_duration,
        first_map_bearing_deg=a0, second_map_bearing_deg=a1,
        apparent_turn_deg=delta, apparent_turn_rate_deg_s=delta / turn_duration,
        first_map_step=hypot(first.dx, first.dy),
        second_map_step=hypot(second.dx, second.dy),
        turn_map_drift=drift,
        direction_matches_key=(delta > 0 if turn_key == 'turn-right' else delta < 0),
        samples=(first, turn, second),
    )
