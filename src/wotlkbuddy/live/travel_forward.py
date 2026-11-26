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


def ghost_route_burst(before, waypoints, index, snapshot, *, guard, fast_observe,
                      expected_map_id, guid, now, travel_input, arrival_yards,
                      right_sign, turn_rate, sleep):
    """Follow consecutive mesh legs with W held across gentle corners.

    Ghosts do not need hostile scans. Stop at sharp corners, stalled movement,
    floor changes, or the final approach. Return the next unreached index and
    measured progress; the caller performs full telemetry after release.
    """
    guard()
    pos,_=fast_observe(snapshot,35.)
    if pos.map_id!=expected_map_id or pos.player_guid.lower()!=guid.lower():
        raise LiveControlError('Fast ghost observation changed map or identity')
    if pos.position.distance_to(before.position)>1.5:
        return None
    target=waypoints[index]
    error=_angle(atan2(target.y-pos.y,target.x-pos.x)-pos.facing_rad)
    if abs(error)>.30 or _distance_xy(pos.position,target)<=arrival_yards:
        return None
    scan=_MOVEMENT_SCANCODES['forward']
    started=now()
    last=pos
    last_moving=started
    last_distance=_distance_xy(pos.position,target)
    turn_key=None
    turn_end=0.
    next_read=started
    steering=0
    reason=''
    travel_input.key_down(scan)
    try:
        for _ in range(168):
            guard()
            t=now()
            if t-started>=5.:
                break
            if turn_key is not None and t>=turn_end:
                travel_input.key_up(turn_key)
                turn_key=None
            if t>=next_read:
                pos,_=fast_observe(snapshot,35.)
                guard()
                if pos.map_id!=expected_map_id or pos.player_guid.lower()!=guid.lower():
                    raise LiveControlError('Fast ghost observation changed map or identity')
                if (_distance_xy(before.position,pos.position)>14*(now()-started)+2 or
                        abs(pos.z-last.z)>3):
                    reason='ghost terrain slide during continuous movement'
                    break
                if _distance_xy(pos.position,last.position)>.12:
                    last_moving=now()
                elif now()-last_moving>.6:
                    reason='no forward progress during continuous ghost movement'
                    break
                distance=_distance_xy(pos.position,target)
                if distance<=arrival_yards+.35:
                    if abs(pos.z-target.z)>5:
                        reason='ghost waypoint height/floor mismatch'
                        break
                    if index==len(waypoints)-1:
                        # The ordinary waypoint loop finishes the exact final
                        # arrival if we stopped a fraction early.
                        if distance<=arrival_yards:
                            index+=1
                        break
                    if distance<=arrival_yards:
                        index+=1
                        target=waypoints[index]
                        last_distance=_distance_xy(pos.position,target)
                    else:
                        # Don't cut a corner before its actual arrival radius.
                        pass
                distance=_distance_xy(pos.position,target)
                if distance>last_distance+1.2:
                    reason='ghost movement diverged from waypoint'
                    break
                last_distance=distance
                error=_angle(atan2(target.y-pos.y,target.x-pos.x)-pos.facing_rad)
                if abs(error)>.40:
                    break
                if turn_key is None and abs(error)>.075:
                    turn_key=_MOVEMENT_SCANCODES['turn-right' if error*right_sign>0 else 'turn-left']
                    travel_input.key_down(turn_key)
                    turn_end=now()+min(.09,max(.035,abs(error)/turn_rate*.70))
                    steering+=1
                last=pos
                next_read=now()+.06
            sleep(.03)
    finally:
        try:
            if turn_key is not None:
                travel_input.key_up(turn_key)
        finally:
            travel_input.key_up(scan)
    guard()
    return index,steering,reason
