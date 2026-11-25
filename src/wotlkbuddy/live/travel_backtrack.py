"""Measured breadcrumb retreat and bounded wall-route replanning."""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, hypot, pi, sin
from typing import Sequence

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition


@dataclass
class BacktrackState:
    current: ClientTelemetry
    world: WorldPosition
    moves: int
    turns: int
    recoveries: int
    waypoints: Sequence[Vec3]
    cautious: bool
    strafes: int
    next_waypoint_index: int


def _distance_xy(a: Vec3, b: Vec3) -> float:
    return hypot(a.x-b.x, a.y-b.y)


def _angle(value: float) -> float:
    return (value + pi) % (2*pi) - pi


def recover_lateral(state, *, repeated, origin, guard, can_traverse,
                    pulse, sample, checked, notify):
    """Test lateral clearance within the current recovery attempt."""
    if repeated or _distance_xy(state.world.position,origin)<.35:
        notify('Backtrack blocked or repeated collision; testing measured lateral clearance')
        lateral=False
        # Same recovery attempt: do not reject it merely because the
        # blocked retreat just recorded this location in the history.
        sides=((-1,'strafe-right'),(1,'strafe-left'))
        if repeated and state.recoveries%2==0:
            sides=tuple(reversed(sides))
        for direction,key in sides:
            for _ in range(5 if repeated else 3):
                guard()
                before=state.world
                angle=before.facing_rad+direction*pi/2
                end=Vec3(before.x+cos(angle)*1.5,before.y+sin(angle)*1.5,before.z)
                try:
                    if not can_traverse(before.position,end):
                        notify(f'  {key}: navmesh blocks lateral step')
                        break
                except (RuntimeError,OSError,ValueError,TimeoutError) as exc:
                    raise LiveControlError(f'Navmesh clearance unavailable during wall recovery: {exc}') from exc
                pulse(key,.18)
                state.strafes+=1
                released=sample()
                state.current,state.world=checked(observation=released,suppress_scan=True)
                actual=released[1]
                d=_distance_xy(before.position,actual.position)
                if d>3 or abs(actual.z-before.z)>2 or abs(_angle(actual.facing_rad-before.facing_rad))>.24:
                    raise LiveControlError('Unexpected movement during wall sidestep')
                if d<.25:
                    notify(f'  {key}: physically blocked')
                    break
                if abs(_angle(atan2(actual.y-before.y,actual.x-before.x)-angle))>.72:
                    notify(f'  {key}: lateral wall slide; trying other clearance')
                    break
                lateral=True
                notify(f'  {key}: measured clearance {d:.2f}y')
            if lateral:
                break
        if not lateral:
            notify('No verified lateral clearance; checking alternate complete paths from current position')


def backtrack_and_replan(state, reason, *, replan_path, can_traverse, max_recoveries,
                         guard, recovery_locations, blocked_waypoints, blocked_edges,
                         breadcrumbs, notify, right_sign, turn_rate, pulse, sample,
                         checked, destination, arrival_yards, validate_nav_path,
                         route_reuses_collision, alternate_wall_route):
    """Retreat toward a recently measured clear position, then reroute.

    No guessed large reverse move. If the collision slid the character
    sideways, face the recorded breadcrumb rather than assume S retraces
    the actual slide. Keep reactive defense, defer proactive pulls until
    the new route is known, and always release keys before sampling.
    """
    if replan_path is None or can_traverse is None or state.recoveries>=max_recoveries:
        return False
    guard()
    origin=state.world.position
    blocked=state.waypoints[state.next_waypoint_index]
    repeated=(any(_distance_xy(origin,p)<3.0 for p in recovery_locations) or
              any(blocked.distance_to(p)<3.0 for p in blocked_waypoints))
    blocked_waypoints.append(blocked)
    blocked_edges.append((origin,atan2(blocked.y-origin.y,blocked.x-origin.x)))
    recovery_locations.append(origin)
    state.recoveries+=1
    nearby=[p for p in reversed(breadcrumbs)
            if 1.0<=_distance_xy(origin,p)<=(10.0 if repeated else 6.0) and abs(origin.z-p.z)<=2.5]
    # Prefer a recently walked point about 2–4 yards behind us.
    nearby.sort(key=lambda p:abs(_distance_xy(origin,p)-(6.0 if repeated else 3.0)))
    target=nearby[0] if nearby else None
    if target is None:
        target=Vec3(state.world.x-cos(state.world.facing_rad)*2.0,
                    state.world.y-sin(state.world.facing_rad)*2.0,state.world.z)
    notify(f'Wall/collision recovery {state.recoveries}/{max_recoveries}: {reason}; '
           'backing toward previously measured terrain')
    for _ in range(24 if repeated else 12):
        guard()
        distance=_distance_xy(state.world.position,target)
        if distance<=.65 or _distance_xy(state.world.position,origin)>=(6.5 if repeated else 3.5):
            break
        heading=atan2(target.y-state.world.y,target.x-state.world.x)
        forward_delta=_angle(heading-state.world.facing_rad)
        reverse_delta=_angle(heading-state.world.facing_rad-pi)
        backward=abs(reverse_delta)<abs(forward_delta)
        delta=reverse_delta if backward else forward_delta
        before=state.world
        if abs(delta)>.24:
            pulse('turn-right' if delta*right_sign>0 else 'turn-left',
                  min(.22,max(.06,abs(delta)/turn_rate*.75)))
            state.turns+=1
            released=sample()
            state.current,state.world=checked(observation=released,suppress_scan=True)
            actual=_angle(released[1].facing_rad-before.facing_rad)
            if abs(actual)<.035 or actual*delta<-.025 or _distance_xy(before.position,released[1].position)>1.2:
                return False
            continue
        # Check the proposed short reverse segment, not the full distance
        # to the breadcrumb (Detour segment_clear is intentionally local).
        step=min(1.0,distance)
        angle=state.world.facing_rad+(pi if backward else 0)
        endpoint=Vec3(state.world.x+cos(angle)*step,state.world.y+sin(angle)*step,state.world.z)
        try:
            if not can_traverse(state.world.position,endpoint):
                # A wall slide may be slightly outside the mesh. A very
                # short retrace of an actually observed movement segment
                # is permissible only toward a nearby measured breadcrumb.
                if not nearby or _distance_xy(state.world.position,target)>2 or abs(state.world.z-target.z)>1.5:
                    break
        except (RuntimeError,OSError,ValueError,TimeoutError):
            break
        held=pulse('backward' if backward else 'forward',.18 if backward else .12)
        state.moves+=1
        released=sample()
        state.current,state.world=checked(observation=released,suppress_scan=True)
        actual=released[1]
        moved=_distance_xy(before.position,actual.position)
        if (moved>max(2.,11*held+1.) or abs(actual.z-before.z)>2 or
                abs(_angle(actual.facing_rad-before.facing_rad))>.24):
            raise LiveControlError('Unexpected movement during wall backtrack')
        if moved<.12:
            break
        if _distance_xy(actual.position,target)>=distance+.15:
            break
        if abs(_angle(atan2(actual.y-before.y,actual.x-before.x)-angle))>.85:
            break
    recover_lateral(state, repeated=repeated, origin=origin, guard=guard,
                    can_traverse=can_traverse, pulse=pulse, sample=sample,
                    checked=checked, notify=notify)
    if _distance_xy(state.world.position,destination)<=arrival_yards and abs(state.world.z-destination.z)<=5:
        state.waypoints=(state.world.position,destination)
    else:
        try:
            raw=replan_path(state.world.position,destination)
            candidate=validate_nav_path(raw,state.world.position,destination,max_step=5.)
            if (_distance_xy(raw[0],state.world.position)>1.8 or abs(raw[0].z-state.world.z)>2.5 or
                    ((repeated or _distance_xy(origin,state.world.position)<.75) and
                     route_reuses_collision(candidate,blocked_edges))):
                raise LiveControlError('Fresh route returns to blocked corridor or snaps away from actual position')
        except (RuntimeError,OSError,ValueError,TimeoutError) as exc:
            notify(f'Rejecting ordinary wall replan: {exc}; searching alternate routes')
            candidate=alternate_wall_route(state.world.position,destination,replan_path=replan_path,
                blocked_edges=blocked_edges,guard=guard,can_traverse=can_traverse,log=notify)
            if candidate is None:
                raise LiveControlError(f'Backtracked but fresh Detour route unavailable: {exc}') from exc
        state.waypoints=candidate
    state.cautious=True
    breadcrumbs[:]=[state.world.position]
    state.next_waypoint_index=1
    notify(f'Obstacle recovery displaced {_distance_xy(origin,state.world.position):.2f}y; '
           f'fresh Detour route from measured XYZ '
           f'({state.world.x:.2f},{state.world.y:.2f},{state.world.z:.2f}), {len(state.waypoints)-1} short legs')
    state.current,state.world=checked(force_scan=True)
    return True
