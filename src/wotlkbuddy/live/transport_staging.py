"""Bounded world-coordinate approach correction for the calibrated lower lift.

Stay on the stationary landing, away from the doorway. Use small ordinary
keyboard pulses and fresh observations; never widen the boarding timing gate.
"""
from math import atan2, cos, sin, hypot
import time

LOWER_TARGET = (1595.6241455, 310.6530457, 4.79105854)
POSITION_TOLERANCE = .15
HEADING_TOLERANCE = .035
MAX_PULSES = 24


def signed_heading_error(facing, target):
    return atan2(sin(target-facing), cos(target-facing))


def staging_area(pos):
    return (pos.map_id == 0 and abs(pos.z+41.424) <= .6
            and 1593.5 <= pos.x <= 1599
            and 309.9 <= pos.y <= 316
            and abs(signed_heading_error(pos.facing_rad, LOWER_TARGET[2])) <= .35)


def staged(pos):
    x, y, facing = LOWER_TARGET
    return (staging_area(pos) and hypot(pos.x-x, pos.y-y) <= POSITION_TOLERANCE
            and abs(signed_heading_error(pos.facing_rad, facing)) <= HEADING_TOLERANCE)


def next_pulse(pos):
    """Return (scan code, duration) or None at target; no input is sent."""
    if not staging_area(pos):
        raise ValueError('Outside bounded lower staging area')
    if staged(pos):
        return None
    x, y, facing = LOWER_TARGET
    error = signed_heading_error(pos.facing_rad, facing)
    if abs(error) > HEADING_TOLERANCE:
        # Standard WoW A increases facing, D decreases it.
        return (0x1E if error > 0 else 0x20, min(.12, max(.008, abs(error)/4.15)))
    dx, dy = x-pos.x, y-pos.y
    f = pos.facing_rad
    forward = dx*cos(f)+dy*sin(f)
    left = -dx*sin(f)+dy*cos(f)
    if abs(forward) >= abs(left):
        return (0x11 if forward > 0 else 0x1F,
                min(.20, max(.025, abs(forward)/7)))
    return (0x10 if left > 0 else 0x12,
            min(.20, max(.025, abs(left)/7)))


def _error(pos):
    x, y, facing = LOWER_TARGET
    return hypot(pos.x-x, pos.y-y)+2*abs(signed_heading_error(pos.facing_rad, facing))


def stage_lower(*, keyboard, read_position, grab, guard, owner, emit, pos):
    """Correct the approach under supervision; return last verified pose."""
    if not staging_area(pos):
        return pos
    stalls = 0
    for _ in range(MAX_PULSES):
        pulse = next_pulse(pos)
        if pulse is None:
            emit('staged_lower', **pos.to_dict())
            return pos
        _, snap, _, _ = grab()
        guard(snap, owner)
        current = read_position()
        if not staging_area(current):
            emit('staging_left_safe_area', **current.to_dict())
            return current
        # Use the latest pose, not the pose from before the screenshot.
        pulse = next_pulse(current)
        if pulse is None:
            emit('staged_lower', **current.to_dict())
            return current
        code, duration = pulse
        guard(snap, owner)
        keyboard.key_down(code)
        try:
            time.sleep(duration)
        finally:
            keyboard.key_up(code)
        time.sleep(.10)
        _, snap, _, _ = grab()
        guard(snap, owner)
        pos = read_position()
        emit('staging_step', **pos.to_dict(), key=code, duration=duration)
        if not staging_area(pos):
            emit('staging_left_safe_area', **pos.to_dict())
            return pos
        stalls = stalls+1 if _error(pos) >= _error(current)-.015 else 0
        if stalls >= 3:
            emit('staging_no_progress', **pos.to_dict())
            return pos
    emit('staging_pulse_limit', **pos.to_dict())
    return pos
