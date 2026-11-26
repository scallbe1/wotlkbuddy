"""Bounded hunt-route retry state and patrol recovery coordination."""
from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_routes import (
    alternate_hunt_routes, recoverable_hunt_failure, encounter_displaced_route, EncounterRouteUnavailable,
    RouteCombatInterrupted, detour_around_encounters)
from wotlkbuddy.horde.hunt_search import next_unvisited_spawn
from wotlkbuddy.horde.hunt_terrain import HuntTerrain
from wotlkbuddy.live.control import LiveControlError


@dataclass
class HuntRouteState:
    recently_visited: dict[tuple[int, int, int], int] = field(default_factory=dict)
    failed_navigation: dict[tuple[int, int, int], LiveControlError] = field(default_factory=dict)
    navigation_mismatches: int = 0
    route_replans: int = 0
    refresh_rounds: int = 0
    force_spawn_search: bool = False
    staging_visited: list[Vec3] = field(default_factory=list)
    required_waits: int = 0
    structured_attempts: int = 0
    terrain: HuntTerrain = field(default_factory=HuntTerrain)


def select_spawn_goal(s, *, routes, nearby, spec, allowed_sources, expected,
                      collect_items, rest, sleep, unhittable_guids,
                      unhittable_positions, log):
    """Refresh transient exclusions within the existing limit, retaining failed routes."""
    if not nearby and routes.refresh_rounds<2:
        routes.refresh_rounds+=1
        log('Local hunting sites exhausted; resting and expanding the profile-constrained search')
        s=rest(s)
        sleep(8)
        routes.recently_visited.clear()
        unhittable_guids.clear()
        unhittable_positions.clear()
        return None
    if not nearby:
        if routes.failed_navigation:
            raise list(routes.failed_navigation.values())[-1]
        raise LiveControlError(
            f'No verified nearby creature spawns or bounded reference '
            f'search locations for XML sources {allowed_sources} '
            f'of item {spec.entry}' if collect_items
            else f'No validated local spawn for objective mob {expected}')
    # Limit repeat failures at one spawn. Respawns are not immediate.
    goal = next_unvisited_spawn(nearby, routes.recently_visited)
    if goal is None:
        if routes.refresh_rounds<2:
            routes.refresh_rounds+=1
            log('All nearby spawn approaches tried; waiting for patrol changes and expanding search')
            s=rest(s)
            sleep(8)
            routes.recently_visited.clear()
            unhittable_guids.clear()
            unhittable_positions.clear()
            return None
        if routes.failed_navigation:
            raise list(routes.failed_navigation.values())[-1]
        raise LiveControlError('No new spawn positions within safe search radius after alternate searches')
    return goal


def handle_spawn_failure(exc, *, routes, key, allowed_sources, capture,
                         guard, world_position, pid, world, log, clear_remaining_aggro=None):
    """Classify a failed approach only after re-verifying live state."""
    # The low-level world follower already released all movement keys
    # on a heading mismatch. Do NOT relax that collision/telemetry
    # guard. Recheck identity, map and actual position, then exclude
    # this spawn for the remainder of the session. The outer loop can
    # search other verified, navmesh-routed spawn candidates.
    height_failure = ('height/floor' in str(exc) or
                      'different heights/floors' in str(exc))
    unsafe_encounter=('unsafe encounter:' in str(exc).lower() or 'possible pack' in str(exc).lower())
    incomplete_route=any(marker in str(exc).lower() for marker in (
        'partial path', 'no usable bounded waypoint path', 'no complete verified route to distant quest camp'))
    if not recoverable_hunt_failure(exc):
        raise exc
    s = capture()
    guard(s)
    current_pid, current_world = world_position(s)
    if current_pid != pid or current_world.map_id != world.map_id:
        raise LiveControlError('Cannot verify map/PID after interrupted navigation') from exc
    if s.player.in_combat:
        if clear_remaining_aggro is None:
            raise LiveControlError('Combat began during off-axis navigation; refusing alternate routing') from exc
        log('Combat interrupted route planning; defending before resuming the same objective')
        s=clear_remaining_aggro(s)
        guard(s)
        current_pid,current_world=world_position(s)
        if current_pid!=pid or current_world.map_id!=world.map_id:
            raise LiveControlError('Cannot verify map/PID after interrupted navigation') from exc
        if s.player.in_combat:
            raise LiveControlError('Aggro unresolved before starting quest phase') from exc
        routes.recently_visited.pop(key,None)
        return
    if 'combat or death present; navigation is not a combat routine' in str(exc).lower():
        routes.recently_visited.pop(key,None)
        log('Combat ended before route retry; refreshing objective without excluding spawn')
        return
    if encounter_displaced_route(exc):
        routes.recently_visited.pop(key,None)
        raise EncounterRouteUnavailable('Encounter displaced route; refreshing same objective from current position') from exc
    routes.recently_visited[key] = 0 if unsafe_encounter and len(allowed_sources)==1 else 2
    if unsafe_encounter:
        routes.route_replans+=1
        routes.force_spawn_search=True
        log(f'Unsafe pack near spawn {key}; planning another quest route from live position')
        return
    routes.failed_navigation[key] = exc
    if incomplete_route:
        log(f'Incomplete Detour route to spawn {key}; selecting another verified quest location')
        return
    if height_failure:
        log(f'Excluding wrong-floor spawn {key}; selecting another verified candidate')
        return
    routes.navigation_mismatches += 1
    log(f'Off-axis movement near spawn {key}; actual player position='
        f'({current_world.x:.2f},{current_world.y:.2f},{current_world.z:.2f}). '
        f'Excluding this spawn; alternative-route attempt '
        f'{routes.navigation_mismatches}/2')
    if routes.navigation_mismatches >= 2:
        raise LiveControlError(
            'Two off-axis navigation failures; stopped to avoid repeated '
            'indoor/collision routing: ' + str(exc)
        ) from exc
    return


def tactical_navigate(driver,navigate,state,destination,path,kind):
    prior=getattr(driver,'hunt_travel_kind',None)
    driver.hunt_travel_kind=kind
    try:
        return navigate(state,destination,approach_path=path)
    finally:
        driver.hunt_travel_kind=prior


def find_hunt_alternative(s, *, routes, world, staging, nearby, hazards, driver,
                          nav, capture, guard, world_position, pid,
                          clear_remaining_aggro, navigate, log):
    """Search once from measured ground, then try a real corridor detour."""
    def supervise():
        current=capture()
        guard(current)
        current_pid,where=world_position(current)
        if current_pid!=pid or where.map_id!=world.map_id:
            raise LiveControlError('Map/PID changed during alternate hunt search')
        if current.player.in_combat:
            raise RouteCombatInterrupted('Aggro/combat detected during route retry')
    log(f'Alternate hunt planning: {len(nearby)} spawn candidates, {len(hazards)} remembered threats')
    try:
        supervise()
        log('Checking local bypass before searching distant spawn staging positions')
        path=detour_around_encounters(nav,world.map_id,world.position,staging,hazards,
            guard=supervise,log=log,max_distance=800.,avoid_radius=15.,candidate_limit=12)
        supervise()
        if path:
            current=capture()
            guard(current)
            if current.player.in_combat:
                raise RouteCombatInterrupted('Aggro/combat detected during route retry')
            log('Verified alternate corridor; following the actual detour path')
            out=tactical_navigate(driver,navigate,current,staging,path,'detour')
            if out.player.in_combat:
                clear_remaining_aggro(out)
            routes.force_spawn_search=True
            return None,True
    except RouteCombatInterrupted:
        current=capture()
        guard(current)
        if current.player.in_combat:
            log('Combat interrupted alternate search; defending before quest work')
            clear_remaining_aggro(current)
        return None,True
    except LiveControlError as exc:
        if not recoverable_hunt_failure(exc):
            raise
        current=capture()
        guard(current)
        if current.player.in_combat:
            clear_remaining_aggro(current)
            return None,True
        log('Intermediate detour interrupted: '+str(exc))
    # The destination may be behind a pack, but a short retreat can change
    # the approach angle. Never discard measured obstacles to force a route.
    from wotlkbuddy.horde.hunt_terrain import retreat_candidates
    from wotlkbuddy.horde.hunt_routes import path_avoids_pack
    from wotlkbuddy.live.world_travel import validate_nav_path
    current=capture()
    guard(current)
    if current.player.in_combat:
        clear_remaining_aggro(current)
        return None,True
    _,where=world_position(current)
    for map_id,point in retreat_candidates(routes.terrain.anchors,current,where,driver,nav,log):
        try:
            supervise()
            if map_id!=where.map_id or not 18<=where.position.distance_to(point)<=65:
                continue
            path=validate_nav_path(nav.find_path(map_id,where.position,point),
                where.position,point,max_distance=90,max_step=9)
            supervise()
            if not path_avoids_pack(path,hazards,radius=15):
                continue
            log(f'Changing approach: retreating {where.position.distance_to(point):.1f}y along a verified route')
            if any(point.distance_to(p)<5 for p in routes.staging_visited):
                continue
            routes.staging_visited.append(point)
            out=tactical_navigate(driver,navigate,capture(),point,path,'retreat')
            if out.player.in_combat:
                clear_remaining_aggro(out)
            routes.force_spawn_search=True
            return None,True
        except RouteCombatInterrupted:
            current=capture()
            guard(current)
            if current.player.in_combat:
                clear_remaining_aggro(current)
            return None,True
        except LiveControlError as exc:
            if not recoverable_hunt_failure(exc):
                raise
            log('Retreat candidate blocked: '+str(exc))
        except (OSError,ValueError,RuntimeError,TimeoutError):
            continue
    try:
        candidates=[p for p in nearby if routes.recently_visited.get((round(p.x),round(p.y),round(p.z)),0)<2][:6]
        alternatives=alternate_hunt_routes(nav,world.map_id,world.position,candidates,hazards,
            routes.recently_visited,excluded_staging=routes.staging_visited,guard=supervise)
        supervise()
        if alternatives:
            return alternatives[0],False
    except RouteCombatInterrupted:
        current=capture()
        guard(current)
        if current.player.in_combat:
            clear_remaining_aggro(current)
        return None,True
    # An exhaustive search from the same position will not improve by
    # blacklisting one remote spawn at a time. Refresh live patrols instead.
    raise EncounterRouteUnavailable('No verified alternate corridor; reobserve patrols before retry')


def run_spawn_route(s, *, routes, goal, nearby, world, reference_only, remote_camp,
                    allowed_sources, driver, nav, now, rest, fresh,
                    clear_remaining_aggro, navigate, capture, guard,
                    world_position, pid, drain_travel_corpses, log):
    """Attempt one bounded spawn approach, then return for a fresh hunt iteration."""
    if len(allowed_sources)==1 and routes.route_replans>=2:
        from wotlkbuddy.horde.structured_approach import explore_required_approach
        if explore_required_approach(s,routes=routes,goal=goal,world=world,
                driver=driver,nav=nav,navigate=navigate,capture=capture,guard=guard,
                world_position=world_position,clear_remaining_aggro=clear_remaining_aggro,
                drain_travel_corpses=drain_travel_corpses,log=log):
            return
    key = (round(goal.x), round(goal.y), round(goal.z))
    dx,dy = world.x-goal.x,world.y-goal.y
    d = hypot(dx,dy)
    if d < 0.1:
        routes.recently_visited[key]=2
        return
    stand_off=min(32.,max(5.,d-3.)) if getattr(driver,'awareness',None) else 10.
    staging = (goal.position if reference_only and remote_camp else
               Vec3(goal.x+dx/d*stand_off,goal.y+dy/d*stand_off,goal.z))
    hazards=[p for p,t in getattr(driver,'dangerous_hunt_positions',[]) if now()-t<90]
    if hazards or routes.route_replans:
        from wotlkbuddy.horde.interaction import navigate_with_status
        alternative,handled=navigate_with_status(driver,'planning',lambda:find_hunt_alternative(
            s,routes=routes,world=world,staging=staging,
            nearby=nearby,hazards=hazards,driver=driver,nav=nav,capture=capture,guard=guard,
            world_position=world_position,pid=pid,clear_remaining_aggro=clear_remaining_aggro,
            navigate=navigate,log=log))
        if handled:
            return
        goal,staging=alternative
        key=(round(goal.x),round(goal.y),round(goal.z))
        log(f'Alternate hunting plan: entry {goal.entry}, ranged staging {staging.distance_to(goal.position):.1f}y from spawn; avoiding {len(hazards)} recent danger points')
    # Route search is synchronous; an enemy can reach us while it runs.
    s=capture()
    guard(s)
    if s.player.in_combat:
        log('Combat began during alternate route selection; defending before navigation')
        clear_remaining_aggro(s)
        routes.recently_visited.pop(key,None)
        return
    routes.recently_visited[key]=routes.recently_visited.get(key,0)+1
    routes.staging_visited.append(staging)
    try:
        s = navigate(s, staging, camp_transit=remote_camp)
    except EncounterRouteUnavailable:
        routes.recently_visited.pop(key,None)
        raise
    except LiveControlError as exc:
        handle_spawn_failure(exc, routes=routes, key=key, allowed_sources=allowed_sources,
                             capture=capture, guard=guard, world_position=world_position,
                             pid=pid, world=world, log=log, clear_remaining_aggro=clear_remaining_aggro)
        return

    if s.player.in_combat:
        s = clear_remaining_aggro(s)
        return
    s=drain_travel_corpses(s)
    if remote_camp:
        _,arrived=world_position(s)
        if arrived.position.distance_to(staging)>8:
            # The first verified segment is not arrival at a camp.
            # Re-evaluate live threats and remaining route on next pass.
            routes.recently_visited.pop(key,None)
            log(f'Continuing toward quest camp; {arrived.position.distance_to(staging):.1f}y remain')
            return
    if reference_only:
        log(f'At source-labeled search area for creature {goal.entry}; '
            'looking for live NPC by exact entry/GUID, '
            'not trusting location as spawn proof')
    else:
        log(f'At candidate spawn for {goal.entry}; will recheck targets')
