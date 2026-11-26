"""Conservative long-range navigation clearance and stationary target facing."""
from math import atan2, hypot, isfinite
from time import sleep as real_sleep

from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES
from wotlkbuddy.live.world_travel import _TURN_CALIBRATIONS, _angle


def ranged_approach_clear(nav, map_id, start, end):
    """A near-straight validated walk route is a geometry hint, NOT spell LOS.

    segment_clear is explicitly a <=2.6 yard sidestep check, not a ranged
    raycast. Do not apply it to the entire 27-yard Fireball corridor.
    """
    distance = start.distance_to(end)
    if not isfinite(distance) or distance > 35:
        return False
    if distance <= 2.6:
        return nav.segment_clear(map_id, start, end)
    path = nav.find_path(map_id, start, end)
    if (len(path) < 2 or path[0].distance_to(start) > 3 or
            path[-1].distance_to(end) > 3):
        return False
    length = sum(a.distance_to(b) for a, b in zip(path, path[1:]))
    if not isfinite(length) or length > distance*1.15+1:
        return False
    dx, dy = end.x-start.x, end.y-start.y
    length2 = dx*dx+dy*dy
    if length2 < .01:
        return False
    for point in path:
        t = max(0., min(1., ((point.x-start.x)*dx+(point.y-start.y)*dy)/length2))
        if hypot(point.x-start.x-t*dx, point.y-start.y-t*dy) > 1.5:
            return False
    return True


def face_target_in_place(state, *, capture, driver, player_position,
                         target_position, log=lambda _: None, sleep=real_sleep):
    """Turn A/D only; verify facing and target identity after each pulse."""
    guid, target_guid = state.player.guid, state.player.target_guid
    keyboard = driver.keyboard
    origin = player_position(state)
    where = origin

    def guard():
        if driver.emergency_stop_requested() or not driver.wow_is_foreground():
            raise LiveControlError('Stationary turn interrupted')

    def pulse(key, duration):
        guard()
        scan = _MOVEMENT_SCANCODES[key]
        keyboard.key_down(scan)
        try:
            sleep(duration)
        finally:
            keyboard.key_up(scan)
        guard()

    def observe():
        guard()
        current = capture()
        if (current.truncated or current.player.dead or current.player.guid != guid):
            raise LiveControlError('Character changed while facing target')
        if current.player.target_guid != target_guid or not current.combat_target or current.combat_target.dead:
            return current, None
        pos = player_position(current)
        if pos.map_id != origin.map_id or pos.position.distance_to(origin.position) > 1.2:
            raise LiveControlError('Unexpected movement during stationary turn')
        return current, pos

    cached = _TURN_CALIBRATIONS.get(keyboard)
    sign, rate = cached[2:] if cached and cached[:2] == (guid, where.map_id) else (None, None)
    for _ in range(12):
        target = target_position(state)
        dx, dy = target.x-where.x, target.y-where.y
        if hypot(dx, dy) < .3:
            return state
        delta = _angle(atan2(dy, dx)-where.facing_rad)
        if abs(delta) <= .35:
            return state
        if sign is None:
            pulse('turn-right', .12)
            state, after = observe()
            if after is None:
                return state
            measured = _angle(after.facing_rad-where.facing_rad)
            if not .07 <= abs(measured) <= 1.3:
                raise LiveControlError('Stationary turn calibration failed')
            sign = 1 if measured > 0 else -1
            rate = min(6., max(1.2, abs(measured)/.12))
            _TURN_CALIBRATIONS[keyboard] = (guid, where.map_id, sign, rate)
            where = after
            continue
        pulse('turn-right' if delta*sign > 0 else 'turn-left',
              min(.85, max(.05, abs(delta)/rate*.94)))
        state, after = observe()
        if after is None:
            return state
        measured = _angle(after.facing_rad-where.facing_rad)
        if abs(measured) < .025 or measured*delta < 0:
            raise LiveControlError('Stationary turn did not face selected target')
        where = after
        log('Turning toward verified enemy without advancing')
    raise LiveControlError('Could not face verified target within stationary turn budget')
