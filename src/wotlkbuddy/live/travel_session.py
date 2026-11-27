"""Navigation session wiring and bounded route lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Callable

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition
from wotlkbuddy.live.travel_state import TravelSessionState


def _distance_xy(a, b):
    return hypot(a.x-b.x, a.y-b.y)


@dataclass
class TravelServices:
    state: TravelSessionState
    destination: Vec3
    breadcrumbs: list[Vec3]
    guard: Callable
    checked: Callable
    pulse: Callable
    smooth_forward: Callable
    recover_collision: Callable
    recover_around_wall: Callable
    right_sign: int
    turn_rate: float
    ghost_route_burst: Callable | None = None


def wire_travel_session(*, first, world0, waypoints, now, notify, expected_map_id,
                        stop_requested, keyboard, start_time, max_seconds, sample,
                        guid, valid_life_state, on_aggro, max_aggro_fights, travel_input,
                        on_threat, threat_check_yards, continuous_forward,
                        expected_quest_id, sleep, fast_observe, arrival_yards,
                        calibration_cache, can_traverse, max_recoveries, replan_path,
                        validate_nav_path, route_reuses_collision, alternate_wall_route):
    """Construct guarded services and calibrate before entering the route loop."""
    from wotlkbuddy.live.travel_state import TravelSessionState
    state = TravelSessionState(first, world0, waypoints)
    recovery_locations: list[Vec3] = []
    blocked_edges: list[tuple[Vec3,float]] = []
    blocked_waypoints: list[Vec3] = []
    from wotlkbuddy.live.travel_observation import TravelObservationState, observe_travel
    observation_state = TravelObservationState(world0.position, now())
    breadcrumbs = [world0.position]
    destination = waypoints[-1]
    notify(f'Navigation goal: map {expected_map_id}, XYZ ({destination.x:.2f},{destination.y:.2f},{destination.z:.2f}); '
           f'from ({world0.x:.2f},{world0.y:.2f},{world0.z:.2f})')

    def guard() -> None:
        if stop_requested() or not keyboard.wow_is_foreground():
            raise LiveControlError('Escape pressed or WoW lost focus during world navigation')
        if now() - start_time > max_seconds:
            raise LiveControlError('World navigation time limit reached')
    def checked(*, force_scan: bool = False, suppress_scan: bool = False,
                observation: tuple[ClientTelemetry, WorldPosition] | None = None
                ) -> tuple[ClientTelemetry, WorldPosition]:
        return observe_travel(
            observation_state, sample=sample, guid=guid, expected_map_id=expected_map_id,
            valid_life_state=valid_life_state, on_aggro=on_aggro,
            max_aggro_fights=max_aggro_fights, guard=guard, notify=notify,
            travel_input=travel_input, on_threat=on_threat,
            threat_check_yards=threat_check_yards, now=now,
            continuous_forward=continuous_forward,
            upcoming_legs=state.upcoming_legs,
            expected_quest_id=expected_quest_id, force_scan=force_scan,
            suppress_scan=suppress_scan, observation=observation)

    from wotlkbuddy.live.travel_motion import movement_pulse, calibrate_turn

    def pulse(key: str, duration: float) -> float:
        return movement_pulse(key, duration, guard=guard, now=now,
                              sleep=sleep, travel_input=travel_input)

    def smooth_forward(before, waypoint, snapshot):
        from wotlkbuddy.live.travel_forward import smooth_forward as burst
        return burst(
            before, waypoint, snapshot, guard=guard, fast_observe=fast_observe,
            expected_map_id=expected_map_id, guid=guid, now=now,
            travel_input=travel_input, arrival_yards=arrival_yards,
            right_sign=right_sign, turn_rate=turn_rate, sleep=sleep)

    def ghost_burst(index):
        from wotlkbuddy.live.travel_forward import ghost_route_burst
        return ghost_route_burst(state.world,state.waypoints,index,state.current,
            guard=guard,fast_observe=fast_observe,expected_map_id=expected_map_id,
            guid=guid,now=now,travel_input=travel_input,arrival_yards=arrival_yards,
            right_sign=right_sign,turn_rate=turn_rate,sleep=sleep,living=not first.player.dead)

    state.current, state.world, state.turns, right_sign, turn_rate = calibrate_turn(
        cache=calibration_cache, keyboard=keyboard, guid=guid,
        current=state.current, world=state.world, turns=state.turns, pulse=pulse,
        checked=checked, notify=notify)

    def recover_around_wall(waypoint: Vec3) -> bool:
        from wotlkbuddy.live.travel_sidestep import recover_sidestep
        return recover_sidestep(
            state, can_traverse=can_traverse, max_recoveries=max_recoveries,
            recovery_locations=recovery_locations, guard=guard, pulse=pulse,
            checked=checked, notify=notify)

    def backtrack_and_replan(reason: str) -> bool:
        from wotlkbuddy.live.travel_backtrack import backtrack_and_replan as recover
        return recover(
            state, reason, replan_path=replan_path, can_traverse=can_traverse,
            max_recoveries=max_recoveries, guard=guard, recovery_locations=recovery_locations,
            blocked_waypoints=blocked_waypoints, blocked_edges=blocked_edges,
            breadcrumbs=breadcrumbs, notify=notify, right_sign=right_sign,
            turn_rate=turn_rate, pulse=pulse, sample=sample, checked=checked,
            destination=destination, arrival_yards=arrival_yards,
            validate_nav_path=validate_nav_path, route_reuses_collision=route_reuses_collision,
            alternate_wall_route=alternate_wall_route)

    def recover_collision(reason):
        # A failed route query does not exhaust physical escape options. Try
        # another measured retreat/side before giving up, within one budget.
        while state.recoveries<max_recoveries:
            guard()
            previous=state.recoveries
            try:
                return backtrack_and_replan(reason)
            except LiveControlError as exc:
                if ('Backtracked but fresh Detour route unavailable:' not in str(exc) or
                        state.recoveries==previous or state.recoveries>=max_recoveries):
                    raise
                notify('Wall route search unsuccessful; trying another measured escape direction')
        return False
    return TravelServices(state, destination, breadcrumbs, guard, checked, pulse,
                          smooth_forward, recover_collision, recover_around_wall,
                          right_sign, turn_rate, ghost_route_burst=ghost_burst)


def run_route(services, *, distance_status, destination_status, on_threat,
              continuous_forward, fast_observe, arrival_yards, sample,
              ghost_only, replan_path, notify, passive_route_scan=False):
    """Execute the active route; replacement routes restart the waypoint cursor."""
    state, destination, breadcrumbs = services.state, services.destination, services.breadcrumbs
    guard, checked, pulse = services.guard, services.checked, services.pulse
    smooth_forward = services.smooth_forward
    recover_collision, recover_around_wall = services.recover_collision, services.recover_around_wall
    right_sign, turn_rate = services.right_sign, services.turn_rate
    # Publish the destination once, before movement. Never interrupt a
    # route to type HUD-only distance updates. Live XYZ remains available
    # to steering, encounter checks, and the console progress log.
    if destination_status is not None:
        guard()
        destination_status(destination)
    elif distance_status is not None:
        # Legacy callers may publish one initial distance snapshot only.
        guard()
        distance_status(destination,state.world.position)
    if on_threat is not None:
        state.current, state.world = checked(force_scan=True)
    if continuous_forward and fast_observe is not None:
        notify('Smooth ghost travel: continuous W across gentle mesh legs; no hostile scans' if ghost_only else
               'Smooth travel: bounded continuous W with passive position and threat checks')
    index=1
    while index<len(state.waypoints):
        waypoint=state.waypoints[index]
        state.next_waypoint_index = index
        notify(f'Action: navigating waypoint {index}/{len(state.waypoints)-1} XYZ '
               f'({waypoint.x:.2f},{waypoint.y:.2f},{waypoint.z:.2f}) toward goal '
               f'({destination.x:.2f},{destination.y:.2f},{destination.z:.2f}); '
               f'{state.world.position.distance_to(destination):.1f}y away')
        if ((ghost_only or passive_route_scan) and continuous_forward and fast_observe is not None
                and services.ghost_route_burst is not None):
            before=state.world
            burst=services.ghost_route_burst(index)
            if burst is not None:
                next_index,steering,reason=burst
                state.moves+=1
                state.turns+=steering
                hazard=reason=='passive hazard'
                state.current,state.world=checked(force_scan=hazard,suppress_scan=passive_route_scan and not reason)
                # Use the fully verified position, not the last fast sample,
                # as the start of every recovery or subsequent burst.
                if _distance_xy(before.position,state.world.position)>.5:
                    breadcrumbs.append(state.world.position)
                    del breadcrumbs[:-24]
                if reason and not hazard:
                    notify('Continuous movement interrupted: '+reason)
                    if recover_collision(reason):
                        index=1
                        state.reached=0
                        continue
                    raise LiveControlError(reason)
                if next_index>index:
                    state.reached+=next_index-index
                    index=next_index
                    state.next_waypoint_index=index
                    if index>=len(state.waypoints):
                        if (_distance_xy(state.world.position,destination)<=arrival_yards and
                                abs(state.world.z-destination.z)<=5):
                            break
                        # Recheck final arrival after releasing movement.
                        index=len(state.waypoints)-1
                        state.reached=max(0,state.reached-1)
                    continue
                # Sharp corners or a final close approach use the original
                # precise movement routine, including measured wall recovery.
        from wotlkbuddy.live.travel_waypoint import execute_waypoint, corner_arrival_yards
        leg_arrival=corner_arrival_yards(state.waypoints,index,arrival_yards,ghost_only=ghost_only,log=notify)
        rerouted = execute_waypoint(
            state, waypoint, index=index, waypoint_count=len(state.waypoints)-1,
            arrival_yards=leg_arrival, guard=guard, turn_rate=turn_rate,
            right_sign=right_sign, pulse=pulse, sample=sample, checked=checked,
            cautious=state.cautious or leg_arrival<arrival_yards, on_threat=on_threat,
            continuous_forward=continuous_forward, ghost_only=ghost_only,
            fast_observe=fast_observe, smooth_forward=smooth_forward,
            recover_collision=recover_collision, replan_path=replan_path,
            recover_around_wall=recover_around_wall, breadcrumbs=breadcrumbs)
        if rerouted:
            index=1
            state.reached=0
            continue
        if abs(state.world.z - waypoint.z) > 5:
            # Include the live-vs-mesh height disagreement so a failed
            # route can be diagnosed without guessing a different floor.
            raise LiveControlError(
                f'Waypoint {index} is on another height/floor: '
                f'live z={state.world.z:.2f}, Detour z={waypoint.z:.2f}, '
                f'delta={state.world.z-waypoint.z:+.2f}y, '
                f'XY remaining={_distance_xy(state.world.position, waypoint):.2f}y')
        state.reached += 1
        if index == len(state.waypoints)-1 or index % 4 == 0:
            notify(f'World waypoint {index}/{len(state.waypoints)-1} reached; '
                   f'position=({state.world.x:.2f},{state.world.y:.2f},{state.world.z:.2f})')
        index+=1
