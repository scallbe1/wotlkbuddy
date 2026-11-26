"""Recover a missed lower boarding attempt before trying another opening."""
from math import atan2, sin, cos, isfinite
import time

BACKWARD = 0x1F
RESET_Y = 310.4


def recoverable_lower(pos):
    heading = pos.facing_rad-4.712389
    return (pos.map_id == 0
            and all(isfinite(v) for v in (pos.x, pos.y, pos.z, pos.facing_rad))
            and abs(pos.x-1596.15) <= 2.8
            and 290 <= pos.y <= 316 and abs(pos.z+41.424) <= .9
            and abs(atan2(sin(heading), cos(heading))) <= .25)


def retreat_lower(*, keyboard, read_position, grab, guard, owner, emit, deadline):
    """Back away from the shaft, waiting at a closed door if necessary."""
    from wotlkbuddy.live.control import LiveControlError
    pos = read_position()
    if not recoverable_lower(pos):
        emit('retreat_unavailable', **pos.to_dict())
        return None
    if pos.y >= RESET_Y:
        return pos
    emit('retreating_from_shaft', **pos.to_dict())
    stop = min(deadline, time.monotonic()+25)
    last_check = float('-inf')
    keyboard.key_down(BACKWARD)
    try:
        while time.monotonic() < stop:
            if keyboard.emergency_stop_requested() or not keyboard.wow_is_foreground():
                raise LiveControlError('Retreat aborted: focus or Escape')
            if time.monotonic()-last_check >= .20:
                _, snap, _, _ = grab()
                guard(snap, owner)
                last_check = time.monotonic()
            pos = read_position()
            if not recoverable_lower(pos):
                emit('retreat_unexpected_position', **pos.to_dict())
                return None
            if pos.y >= RESET_Y:
                emit('returned_to_lower_landing', **pos.to_dict())
                return pos
            time.sleep(.035)
        emit('retreat_timeout', **pos.to_dict())
        return None
    finally:
        keyboard.key_up(BACKWARD)
