"""Bounded movement pulses and verified turn calibration."""
from __future__ import annotations

from math import hypot, pi

from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES


def _angle(value):
    return (value + pi) % (2*pi) - pi


def _distance_xy(a, b):
    return hypot(a.x-b.x, a.y-b.y)


def movement_pulse(key, duration, *, guard, now, sleep, travel_input):
    """Hold one movement key for a bounded *wall-clock* interval.

    Previously we read live telemetry while W was down. Each live
    sample performs a second memory observation after a 120ms wait,
    plus an image capture. That blocking time was omitted from the
    nominal hold duration, so a requested 1.2-second pulse could
    move the character for multiple seconds and trip the 12-yard
    jump check on a perfectly valid Detour path.

    Never perform blocking telemetry capture with a movement key
    down. Check focus/Escape/time limit every <=40ms, release the
    key unconditionally, then let checked() observe and handle aggro.
    No movement key stays down during a threat scan or client read.
    """
    guard()
    scan = _MOVEMENT_SCANCODES[key]
    started = now()
    travel_input.key_down(scan)
    try:
        accounted = 0.0
        while accounted < duration:
            guard()
            # Both clocks bound the hold: a real elapsed-time deadline
            # handles slow/suspended sleeps, while the finite duration
            # budget supports deterministic simulated/frozen test clocks.
            left = min(duration - accounted, duration - (now() - started))
            if left <= 0:
                break
            step = min(0.04, left)
            sleep(step)
            accounted += step
    finally:
        travel_input.key_up(scan)
    held = now() - started
    guard()
    return held


def calibrate_turn(*, cache, keyboard, guid, current, world, turns,
                   pulse, checked, notify):
    """Reuse only verified same-character/map/keyboard calibration."""
    # Reusing measured calibration across nearby Detour legs avoids a
    # visible 0.12s D-key twitch before every corpse and vendor approach.
    # Character/map/keyboard must all match; real turn feedback remains
    # checked for directional agreement on every steering correction.
    try:
        cached = cache.get(keyboard)
    except TypeError:
        cached = None  # unhashable/extension keyboard: calibrate normally
    if cached is not None and cached[:2] == (guid, world.map_id):
        right_sign, turn_rate = cached[2:]
        notify(f'Reusing verified turn calibration: direction={right_sign:+d}, '
               f'rate={turn_rate:.2f} rad/s')
    else:
        notify('Calibrating D-turn using verified live facing (no forward movement)')
        before = world
        pulse('turn-right', 0.12)
        current, world = checked()
        calibration = _angle(world.facing_rad - before.facing_rad)
        if not 0.09 <= abs(calibration) <= 1.3:
            raise LiveControlError('Turn key did not produce a plausible facing change')
        if _distance_xy(before.position, world.position) > 1.2:
            raise LiveControlError('Player translated unexpectedly while calibrating turn')
        right_sign = 1 if calibration > 0 else -1
        turn_rate = min(6.0, max(1.2, abs(calibration) / 0.12))
        turns += 1
        try:
            cache[keyboard] = (
                guid, world.map_id, right_sign, turn_rate)
        except TypeError:
            pass
        notify(f'Calibrated turn direction={right_sign:+d}, rate={turn_rate:.2f} rad/s')
    return current, world, turns, right_sign, turn_rate
