"""Execute one validated waypoint with measured movement and bounded recovery."""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, hypot, pi

from wotlkbuddy.live.control import LiveControlError


@dataclass
class WaypointState:
    current: object
    world: object
    moves: int
    turns: int


def _distance_xy(a, b):
    return hypot(a.x-b.x, a.y-b.y)


def _angle(value):
    return (value + pi) % (2*pi) - pi


def execute_waypoint(state, waypoint, *, index, waypoint_count, arrival_yards,
                     guard, turn_rate, right_sign, pulse, sample, checked, cautious,
                     on_threat, continuous_forward, ghost_only, fast_observe,
                     smooth_forward, recover_collision, replan_path,
                     recover_around_wall, breadcrumbs):
    """Return True on a replacement route; otherwise finish the current leg."""
    count = 0
    stalls = 0
    prev_distance = _distance_xy(state.world.position, waypoint)
    while _distance_xy(state.world.position, waypoint) > arrival_yards:
        guard()
        if count >= 70:
            raise LiveControlError(f'Waypoint {index} stalled (70 pulses)')
        # A vertical-only or unreachable waypoint isn't a reason to
        # walk indefinitely; avoid confusing Z with horizontal arrival.
        dxy = _distance_xy(state.world.position, waypoint)
        if dxy <= arrival_yards and abs(state.world.z - waypoint.z) > 5:
            raise LiveControlError('Waypoint reached in 2D but wrong height/floor')
        desired = atan2(waypoint.y - state.world.y, waypoint.x - state.world.x)
        delta = _angle(desired - state.world.facing_rad)
        before = state.world
        if abs(delta) > 0.24:
            duration = min(0.22, max(0.06, abs(delta) / turn_rate * 0.75))
            key = 'turn-right' if delta * right_sign > 0 else 'turn-left'
            pulse(key, duration)
            state.turns += 1
            released = sample()
            state.current, state.world = checked(observation=released)
            delta_actual = _angle(released[1].facing_rad - before.facing_rad)
            if abs(delta_actual) < 0.035 or delta_actual * delta < -0.025:
                raise LiveControlError('Turn key did not rotate toward waypoint')
            if _distance_xy(before.position, released[1].position) > 2:
                raise LiveControlError('Unexpected player displacement during turn')
        else:
            # Smooth, distance-dependent forward holds reduce the
            # start/stop jitter caused by fixed 0.20 s taps on every
            # Detour waypoint. Never change the validated path or
            # skip safety checks: pulse() checks focus/Escape every
            # 40 ms, checked() observes combat and live position after
            # each hold, and short waypoint approaches use short holds.
            #
            # Use a conservative observed walking speed budget of
            # 8 yards/s and stop with a safety margin; this does NOT
            # assume the player will move exactly that far.
            forward_duration = min(
                0.25 if cautious else ((0.60 if on_threat is not None else 1.20) if continuous_forward else 0.45),
                max(0.10 if ghost_only else 0.18,
                     0.75*(dxy-arrival_yards)/(11.0 if ghost_only else 8.0)))
            burst=None
            if (continuous_forward and not cautious and not ghost_only and
                    fast_observe is not None and dxy>5 and abs(delta)<=.24):
                burst=smooth_forward(before,waypoint,state.current)
            if burst is None:
                held=pulse('forward',forward_duration)
                interrupted=False
            else:
                held,interrupted,steering=burst
                state.turns+=steering
            state.moves += 1
            # Measure immediately after releasing W, BEFORE the
            # reactive handler or proactive scan can alter position.
            # checked() can reuse the identical live observation and
            # still run normal combat, identity and quest guards.
            released = sample()
            state.current, state.world = checked(observation=released,force_scan=interrupted)
            moved_to = released[1]
            displacement = _distance_xy(before.position, moved_to.position)
            # This bound is tied to the actual key-down interval,
            # rather than a hardcoded 12 yards regardless of timing.
            # 11 yd/s is above normal unmounted running speed; the
            # additional 2 yards cover polling/snapshot jitter.
            allowed = max(4.0, (14.0 if ghost_only else 11.0) * held + 2.0)
            if displacement > allowed:
                raise LiveControlError(
                    'Implausible world jump during forward movement: '
                    f'{displacement:.2f}y in {held:.2f}s of W '
                    f'(limit {allowed:.2f}y; '
                    f'from {before.x:.2f},{before.y:.2f} '
                    f'to {moved_to.x:.2f},{moved_to.y:.2f}; '
                    f'waypoint {index}/{waypoint_count})')
            stalls = stalls + 1 if displacement < 0.15 else 0
            if stalls >= 4:
                if recover_collision('no forward progress'):
                    return True
                if replan_path is None and recover_around_wall(waypoint):
                    stalls = 0
                    prev_distance = _distance_xy(state.world.position, waypoint)
                    count += 1
                    continue
                raise LiveControlError('No forward progress: local obstacle recovery failed or disabled')
            if displacement > 0.2:
                forward_angle = atan2(moved_to.y-before.y,
                                      moved_to.x-before.x)
                if abs(_angle(forward_angle - before.facing_rad)) > 0.85:
                    if recover_collision('wall slide: measured movement disagrees with facing'):
                        return True
                    raise LiveControlError('Measured movement disagrees with client facing')
                if _distance_xy(state.world.position,breadcrumbs[-1])>=.5:
                    breadcrumbs.append(state.world.position)
                    del breadcrumbs[:-24]
        count += 1
        distance = _distance_xy(state.world.position, waypoint)
        if distance > prev_distance + 1.0:
            if recover_collision(f'movement diverged from waypoint {index}'):
                return True
            raise LiveControlError(f'Movement diverged from waypoint {index}')
        prev_distance = distance
    return False
