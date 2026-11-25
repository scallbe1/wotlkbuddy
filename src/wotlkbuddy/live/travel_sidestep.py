"""Bounded, measured local obstacle sidestep recovery."""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, hypot, pi, sin

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition


@dataclass
class SidestepState:
    current: ClientTelemetry
    world: WorldPosition
    moves: int
    recoveries: int
    strafes: int


def _distance_xy(a: Vec3, b: Vec3) -> float:
    return hypot(a.x-b.x, a.y-b.y)


def _angle(value: float) -> float:
    return (value + pi) % (2*pi) - pi


def recover_sidestep(state: SidestepState, *, can_traverse, max_recoveries,
                     recovery_locations, guard, pulse, checked, notify) -> bool:
    """Bounded, measured Q/E sidestep of a *navmesh-checked* local segment.

    Only enable via a live Detour query; never guess that a sidestep is safe.
    This is local dynamic-obstacle recovery, not a substitute for navmesh routing.
    """
    if can_traverse is None or max_recoveries == 0:
        return False
    if state.recoveries >= max_recoveries:
        raise LiveControlError('Obstacle recovery budget exhausted')
    if any(_distance_xy(state.world.position, old) < 2.0 for old in recovery_locations):
        raise LiveControlError('Obstacle persists near previously recovered location')
    recovery_locations.append(state.world.position)
    state.recoveries += 1
    notify(f'Obstacle recovery {state.recoveries}/{max_recoveries}: try E/right '
           'sidestep, then Q/left if blocked (maximum 3 pulses per side)')

    def mesh_clear(start: Vec3, end: Vec3) -> bool:
        guard()
        try:
            return can_traverse(start, end)
        except (RuntimeError, OSError, ValueError, TimeoutError) as exc:
            raise LiveControlError(f'Navmesh clearance unavailable during recovery: {exc}') from exc

    # Right first, matching common wall corner avoidance. Do not cross a
    # wall/ledge just because a navmesh query returns a nearby polygon.
    for direction, key in ((-1, 'strafe-right'), (1, 'strafe-left')):
        moved_laterally = False
        for step in range(3):
            guard()
            before = state.world
            angle = before.facing_rad + direction * pi / 2
            ahead = Vec3(before.x + cos(angle)*1.7,
                         before.y + sin(angle)*1.7, before.z)
            if not mesh_clear(before.position, ahead):
                notify(f'  {key}: navmesh blocks proposed sideways step')
                break
            pulse(key, 0.18)
            state.strafes += 1
            state.current, state.world = checked()
            delta = _distance_xy(before.position, state.world.position)
            if delta > 3.0 or abs(state.world.z - before.z) > 2.0:
                raise LiveControlError('Unexpected movement during obstacle sidestep')
            if abs(_angle(state.world.facing_rad - before.facing_rad)) > 0.24:
                raise LiveControlError('Q/E key did not strafe: verify WoW movement bindings')
            if delta < 0.25:
                notify(f'  {key}: blocked in game (moved {delta:.2f}y)')
                break
            travelled = atan2(state.world.y-before.y, state.world.x-before.x)
            if abs(_angle(travelled-angle)) > 0.72:
                raise LiveControlError('Sideways displacement disagrees with key direction')
            moved_laterally = True
            notify(f'  {key} step {step+1}: moved {delta:.2f}y; '
                   f'position=({state.world.x:.2f},{state.world.y:.2f},{state.world.z:.2f})')
            # Probe forward in current facing; if a real wall still blocks
            # progress, continue laterally rather than steering into it.
            probe = Vec3(state.world.x + cos(state.world.facing_rad)*1.6,
                         state.world.y + sin(state.world.facing_rad)*1.6, state.world.z)
            if not mesh_clear(state.world.position, probe):
                continue
            before = state.world
            pulse('forward', 0.20)
            state.moves += 1
            state.current, state.world = checked()
            advanced = _distance_xy(before.position, state.world.position)
            if advanced > 3.0 or abs(state.world.z-before.z) > 2.0:
                raise LiveControlError('Unexpected movement during forward clearance probe')
            if advanced >= 0.50:
                # Clear the wall's leading corner before steering toward
                # the original waypoint. One successful forward pulse
                # followed by a diagonal turn can hit the *same* wall.
                staging = advanced
                staging_blocked = False
                for _ in range(2):
                    before = state.world
                    probe = Vec3(state.world.x + cos(state.world.facing_rad)*1.6,
                                 state.world.y + sin(state.world.facing_rad)*1.6, state.world.z)
                    if not mesh_clear(state.world.position, probe):
                        staging_blocked = True
                        break
                    pulse('forward', 0.20)
                    state.moves += 1
                    state.current, state.world = checked()
                    delta_forward = _distance_xy(before.position, state.world.position)
                    if delta_forward > 3.0 or abs(state.world.z-before.z) > 2.0:
                        raise LiveControlError('Unexpected staging movement past obstacle')
                    if delta_forward < 0.50:
                        staging_blocked = True
                        break
                    staging += delta_forward
                if not staging_blocked:
                    notify(f'  Obstacle bypass: advanced {staging:.2f}y past wall; '
                           'resuming Detour waypoints')
                    return True
                notify('  Still next to obstruction after forward probe; '
                       'trying another small lateral step')
        # If right achieved a meaningful lateral displacement but the
        # forward probe still fails, do NOT blindly strafe back left over
        # the same area; keep the measured stop position for troubleshooting.
        if moved_laterally:
            break
    return False
