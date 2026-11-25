"""Bounded hunt-route retry state and patrol recovery coordination."""
from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_routes import alternate_hunt_routes, recoverable_hunt_failure
from wotlkbuddy.horde.hunt_search import next_unvisited_spawn
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
                         guard, world_position, pid, world, log):
    """Classify a failed approach only after re-verifying live state."""
    # The low-level world follower already released all movement keys
    # on a heading mismatch. Do NOT relax that collision/telemetry
    # guard. Recheck identity, map and actual position, then exclude
    # this spawn for the remainder of the session. The outer loop can
    # search other verified, navmesh-routed spawn candidates.
    height_failure = ('height/floor' in str(exc) or
                      'different heights/floors' in str(exc))
    unsafe_encounter=('unsafe encounter:' in str(exc).lower() or 'possible pack' in str(exc).lower())
    if not recoverable_hunt_failure(exc):
        raise exc
    s = capture()
    guard(s)
    current_pid, current_world = world_position(s)
    if current_pid != pid or current_world.map_id != world.map_id:
        raise LiveControlError('Cannot verify map/PID after interrupted navigation') from exc
    if s.player.in_combat:
        raise LiveControlError(
            'Combat began during off-axis navigation; refusing alternate routing'
        ) from exc
    routes.recently_visited[key] = 0 if unsafe_encounter and len(allowed_sources)==1 else 2
    if unsafe_encounter:
        routes.route_replans+=1
        routes.force_spawn_search=True
        log(f'Unsafe pack near spawn {key}; planning another quest route from live position')
        return
    routes.failed_navigation[key] = exc
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


def run_spawn_route(s, *, routes, goal, nearby, world, reference_only, remote_camp,
                    allowed_sources, driver, nav, now, rest, fresh,
                    clear_remaining_aggro, navigate, capture, guard,
                    world_position, pid, drain_travel_corpses, log):
    """Attempt one bounded spawn approach, then return for a fresh hunt iteration."""
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
        alternatives=alternate_hunt_routes(nav,world.map_id,world.position,nearby,hazards,routes.recently_visited,
            excluded_staging=routes.staging_visited)
        if not alternatives:
            if len(allowed_sources)==1 and routes.required_waits<4:
                routes.required_waits+=1
                log('Required target has no safe firing position yet; observing patrol movement and recovering before retry')
                s=rest(s,full=True)
                for _ in range(6):
                    s=fresh(s,3)
                    if s.player.in_combat:
                        s=clear_remaining_aggro(s)
                        break
                routes.staging_visited.clear()
                routes.recently_visited.clear()
                driver.dangerous_hunt_positions=[]  # live scanner must re-observe, not trust stale patrols
                return
            routes.recently_visited[key]=2
            log('No safe approach to this spawn cluster; searching remaining quest locations')
            return
        goal,staging=alternatives[0]
        key=(round(goal.x),round(goal.y),round(goal.z))
        log(f'Alternate hunting plan: entry {goal.entry}, ranged staging {staging.distance_to(goal.position):.1f}y from spawn; avoiding {len(hazards)} recent danger points')
    routes.recently_visited[key]=routes.recently_visited.get(key,0)+1
    routes.staging_visited.append(staging)
    try:
        s = navigate(s, staging, camp_transit=remote_camp)
    except LiveControlError as exc:
        handle_spawn_failure(exc, routes=routes, key=key, allowed_sources=allowed_sources,
                             capture=capture, guard=guard, world_position=world_position,
                             pid=pid, world=world, log=log)
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
