"""Bounded continuous forward movement using passive position observations."""
from __future__ import annotations

from math import atan2, hypot, pi

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES


def _distance_xy(a: Vec3, b: Vec3) -> float:
    return hypot(a.x-b.x, a.y-b.y)


def _angle(value: float) -> float:
    return (value + pi) % (2*pi) - pi


def smooth_forward(before, waypoint, snapshot, *, guard, fast_observe,
                   expected_map_id, guid, now, travel_input, arrival_yards,
                   right_sign, turn_rate, sleep):
    """Bounded W hold with cheap position/hazard observations only.

    No screenshots, chat, combat callbacks or navmesh RPC while W is down.
    A full addon sample is still required after every burst.
    """
    guard()
    try:
        measured, hazard = fast_observe(snapshot, 35.)
    except (OSError, ValueError, RuntimeError):
        return None
    if hazard or measured.position.distance_to(before.position)>1.2:
        return None
    if measured.map_id!=expected_map_id or measured.player_guid.lower()!=guid.lower():
        raise LiveControlError('Fast movement observation changed map or identity')
    scan=_MOVEMENT_SCANCODES['forward']
    began=now()
    last=measured
    turn_key=None
    turn_end=0.
    next_observation=began
    interrupted=False
    steering=0
    travel_input.key_down(scan)
    try:
        for _ in range(68):
            if now()-began>=2.0:
                break
            guard()
            t=now()
            if turn_key is not None and t>=turn_end:
                travel_input.key_up(turn_key)
                turn_key=None
            if t>=next_observation:
                # Local read-only observation. Unknown or slow reads
                # terminate the burst rather than extending blind travel.
                measured,hazard=fast_observe(snapshot,35.)
                guard()
                if (measured.map_id!=expected_map_id or
                        measured.player_guid.lower()!=guid.lower()):
                    raise LiveControlError('Fast movement observation changed map or identity')
                if hazard:
                    interrupted=True
                    break
                if (abs(measured.z-last.z)>2.5 or
                        _distance_xy(measured.position,before.position)>11*(now()-began)+2):
                    raise LiveControlError('Implausible movement during continuous W')
                distance=_distance_xy(measured.position,waypoint)
                if distance<=arrival_yards+0.8:
                    break
                heading=atan2(waypoint.y-measured.y,waypoint.x-measured.x)
                error=_angle(heading-measured.facing_rad)
                if abs(error)>.35:
                    break  # sharp corner: stop and use measured stationary turn
                if turn_key is None and .10<abs(error)<=.35 and distance>4:
                    turn_key=_MOVEMENT_SCANCODES['turn-right' if error*right_sign>0 else 'turn-left']
                    travel_input.key_down(turn_key)
                    turn_end=now()+min(.09,max(.035,abs(error)/turn_rate*.65))
                    steering+=1
                last=measured
                next_observation=now()+.12
            sleep(.03)
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc,LiveControlError):
            raise
        raise LiveControlError(f'Fast movement observation failed: {exc}') from exc
    finally:
        try:
            if turn_key is not None:
                travel_input.key_up(turn_key)
        finally:
            travel_input.key_up(scan)
    guard()
    return now()-began,interrupted,steering
