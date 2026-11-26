"""Practical, supervised lower-to-upper elevator traversal.

Let the game's door collision govern passage rather than requiring a
camera template or synchronized clock. Verify the actual coordinate-frame
transitions, steer back toward the deck centreline, and bound the attempt.
Collision behavior is an assumption to validate on this client.
"""
from math import atan2, sin, cos, isfinite
import time

FORWARD, LEFT, RIGHT = 0x11, 0x10, 0x12


def _heading_error(facing, target):
    return abs(atan2(sin(facing-target), cos(facing-target)))


def route_region(pos):
    """Classify only the known straight shaft route, not arbitrary terrain."""
    if pos.map_id != 0 or not all(isfinite(v) for v in (
            pos.x, pos.y, pos.z, pos.facing_rad)):
        return 'unknown'
    if (abs(pos.x-1596.15) <= 2.5
            and _heading_error(pos.facing_rad, 4.712389) <= .25):
        # The doorway is not the transport attachment boundary. The live
        # client still reported world XYZ at Y=299.766 after passing the door.
        # Continue toward the shaft centre (291.8) until attachment is observed.
        if abs(pos.z+41.424) <= .9 and 291.8 <= pos.y <= 316:
            return 'lower'
        if abs(pos.z-55.24) <= .9 and 278 <= pos.y <= 285.5:
            return 'upper'
    if (-10.5 <= pos.x <= 10.5 and abs(pos.y) <= 3
            and abs(pos.z+.464) <= .35
            and _heading_error(pos.facing_rad, 0) <= .20):
        return 'deck'
    return 'unknown'


def next_key(pos, region, correcting=False):
    """Use a lateral correction rather than abandon a viable deck crossing."""
    if region == 'deck' and abs(pos.y) > (.65 if correcting else 1.25):
        return RIGHT if pos.y > 0 else LEFT
    return FORWARD


def traverse(*, keyboard, read_position, grab, guard, owner, emit, seconds):
    """Hold movement toward the exit across door cycles; always release input.

    Only allow lower -> deck -> upper transitions. If a door does not block
    passage as expected, an unexpected coordinate or fall aborts the attempt.
    """
    from wotlkbuddy.live.control import LiveControlError

    deadline = time.monotonic()+seconds
    held = None
    region = 'lower'
    last_check = last_report = float('-inf')
    pos = read_position()
    if route_region(pos) != 'lower':
        emit('collision_route_start_invalid', **pos.to_dict())
        return
    emit('collision_traversal_started', **pos.to_dict())
    try:
        while time.monotonic() < deadline:
            if keyboard.emergency_stop_requested() or not keyboard.wow_is_foreground():
                raise LiveControlError('Traversal aborted: focus or Escape')
            now = time.monotonic()
            if now-last_check >= .20:
                _, snap, _, _ = grab()
                guard(snap, owner)
                last_check = time.monotonic()
                if last_check >= deadline:
                    break
            pos = read_position()
            observed = route_region(pos)
            if observed not in {'lower': ('lower', 'deck'),
                                'deck': ('deck', 'upper'),
                                'upper': ('upper',)}[region]:
                emit('collision_route_unexpected_position',
                     previous_region=region, **pos.to_dict())
                return
            if observed != region:
                region = observed
                emit('boarded_transport' if region == 'deck' else 'reached_upper',
                     **pos.to_dict())
            if region == 'upper' and pos.y <= 280.8:
                emit('exited_upper', **pos.to_dict())
                return
            key = next_key(pos, region, correcting=held in (LEFT, RIGHT))
            if key != held:
                if held is not None:
                    keyboard.key_up(held)
                    held = None
                keyboard.key_down(key)
                held = key
            if now-last_report >= 2:
                emit('collision_progress', region=region, key=key, **pos.to_dict())
                last_report = now
            time.sleep(.035)
        emit('collision_route_timeout', region=region, **pos.to_dict())
    finally:
        if held is not None:
            keyboard.key_up(held)
