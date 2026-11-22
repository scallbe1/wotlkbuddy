"""Bounded, observer-driven movement experiment, not waypoint navigation.

Never call this module from the autonomous QuestBot. A human must arm each
short session, on safe open ground, with WoW in the foreground. All movement
is capped; a stale view, focus loss, changed character/zone, or combat aborts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import (
    KeyboardDriver, LiveControlError, MovementProbeResult, probe_movement,
)


@dataclass(frozen=True, slots=True)
class BurstReport:
    character: str
    zone: str
    key: str
    pulse_duration: float
    pulses_requested: int
    pulses_sent: int
    stopped_reason: str
    start_map_x: float
    start_map_y: float
    end_map_x: float
    end_map_y: float
    total_map_distance: float
    samples: tuple[MovementProbeResult, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def bounded_burst(
    *,
    capture: Callable[[], ClientTelemetry],
    keyboard: KeyboardDriver,
    pulses: int = 3,
    duration: float = 0.15,
    key: str = 'forward',
    max_map_distance: float = 0.002,
    minimum_progress: float = 0.000015,
    sleep: Callable[[float], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> BurstReport:
    """Perform at most six *independently validated* short movement pulses.

    Positions are normalized zone-map coordinates, NOT physical distances or
    coordinates suitable for a pathfinding mesh. A single failed/partial pulse
    aborts rather than re-sending. Escape is checked between pulses; Ctrl+C
    interrupts a held pulse and ``probe_movement`` releases the key in finally.
    """
    if not isinstance(pulses, int) or not 1 <= pulses <= 6:
        raise ValueError('pulses must be an integer between 1 and 6')
    if not 0.05 <= duration <= 0.20:
        raise ValueError('duration must be 0.05..0.20 seconds')
    if key not in {'forward', 'backward'}:
        raise ValueError('bounded burst only supports forward or backward movement')
    if not 0.00005 <= max_map_distance <= 0.01:
        raise ValueError('max_map_distance must be 0.00005..0.01')
    if not 0.0 < minimum_progress < max_map_distance:
        raise ValueError('minimum_progress must be positive and smaller than max_map_distance')
    # Limit the total possible duration of key-down events.
    if pulses * duration > 1.2 + 1e-10:
        raise ValueError('total movement time must not exceed 1.2 seconds')

    from time import sleep as real_sleep
    pause = real_sleep if sleep is None else sleep
    is_stopped = stop_requested if stop_requested is not None else (lambda: False)

    # A safe baseline must be observed before *any* key is pressed.
    if not keyboard.wow_is_foreground():
        raise LiveControlError('WoW must be foreground before movement')
    baseline = capture()
    from wotlkbuddy.live.control import _validate_telemetry
    _validate_telemetry(baseline)

    origin = baseline.player
    samples: list[MovementProbeResult] = []
    last_x, last_y = origin.map_x, origin.map_y
    distance = 0.0
    stalled = 0
    reason = 'pulse_limit'

    def guarded_pause(seconds: float) -> None:
        # The single-pulse driver always key-ups in finally. Checking every
        # 30ms bounds how long an E-stop or focus loss can leave a key held.
        remaining = seconds
        while remaining > 1e-9:
            if is_stopped():
                raise LiveControlError('Emergency stop requested (Escape)')
            if not keyboard.wow_is_foreground():
                raise LiveControlError('WoW focus lost during movement')
            chunk = min(0.03, remaining)
            pause(chunk)
            remaining -= chunk

    for _ in range(pulses):
        if is_stopped():
            reason = 'user_stop'
            break
        if not keyboard.wow_is_foreground():
            raise LiveControlError('WoW focus lost between pulses')
        # Read current state again; do not move if combat began between pulses.
        current = capture()
        _validate_telemetry(current)
        if (current.player.guid != origin.guid or current.player.continent != origin.continent
                or current.player.map_zone != origin.map_zone):
            raise LiveControlError('Character or map changed during movement session')
        if hypot(current.player.map_x - last_x, current.player.map_y - last_y) > 0.0003:
            raise LiveControlError('Uncommanded map position change; stopping movement')

        sample = probe_movement(key=key, duration=duration, capture=capture,
                                keyboard=keyboard, sleep=guarded_pause)
        if sample.character != origin.name or sample.zone != origin.zone_name:
            raise LiveControlError('Player identity or zone changed after movement')
        samples.append(sample)
        last_x, last_y = sample.after_map_x, sample.after_map_y
        step = hypot(sample.dx, sample.dy)
        distance += step
        stalled = stalled + 1 if step < minimum_progress else 0
        if stalled >= 2:
            reason = 'stalled'
            break
        if distance >= max_map_distance:
            reason = 'distance_budget'
            break

    return BurstReport(
        character=origin.name, zone=origin.zone_name, key=key,
        pulse_duration=duration, pulses_requested=pulses,
        pulses_sent=len(samples), stopped_reason=reason,
        start_map_x=origin.map_x, start_map_y=origin.map_y,
        end_map_x=last_x, end_map_y=last_y,
        total_map_distance=distance, samples=tuple(samples),
    )
