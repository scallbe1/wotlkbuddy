"""Verified, attended Detour visit to a named NPC using local creature spawns.

No guessed straight lines or click-at-coordinate interactions. The observed
target GUID must contain the exact NPC creature template ID.
"""
from __future__ import annotations

from math import hypot
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.horde.interaction import navigate_with_status
from wotlkbuddy.horde.height_recovery import HeightRecovery
from wotlkbuddy.horde.worlddata import distance_world
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.transport_route import TransportRequired
from wotlkbuddy.live.transport_plan import elevator_source
from wotlkbuddy.horde.transport_visit import walk_with_elevator
from wotlkbuddy.live.world_position import verified_live_position
from wotlkbuddy.live.world_travel import validate_nav_path, follow_world_path
from wotlkbuddy.horde.combat import creature_entry_from_guid
from wotlkbuddy.horde.dialog_cleanup import dismiss_quest_dialog
from wotlkbuddy.horde.return_turnin import encounter_route_failure, height_route_failure
from wotlkbuddy.horde.hunt_routes import (unsafe_route_encounter, detour_around_encounters,
                                         EncounterRouteUnavailable)


def resolve_npc_spawn(spawns, world, npc_id, npc_name, radius, destination_hint):
    matches = [spot for spot in spawns.find(npc_id, world.map_id, 100)
               if distance_world(world.position, spot.position) <= radius]
    if destination_hint is not None:
        # The source profile's NPC position was independently matched against
        # the spawn DB by the NPC catalog. Preserve that identity throughout
        # navigation instead of silently picking a different nearby spawn.
        matches = [spot for spot in matches
                   if distance_world(destination_hint, spot.position) <= 10]
    if not matches:
        raise LiveControlError(f'No verified nearby spawn of {npc_name} ({npc_id})')
    matches.sort(key=lambda spot: distance_world(
        destination_hint if destination_hint is not None else world.position,
        spot.position))
    dest = matches[0].position
    if any(distance_world(dest, other.position) > 10 for other in matches):
        raise LiveControlError(
            f'Multiple ambiguous locations for NPC {npc_name}; refusing to guess')
    return dest


class NpcTargetUnavailable(LiveControlError):
    """NPC could not be selected after a bounded verified visit."""


def verified_npc_route(raw,start,dest, *, map_id=None):
    """Unusable destination geometry is recoverable; invalid live state is not."""
    if elevator_source(raw, start, dest, map_id):
        return tuple(raw)
    try:
        return validate_nav_path(raw,start,dest,max_step=18.)
    except TransportRequired as exc:
        raise EncounterRouteUnavailable(str(exc)) from exc
    except (LiveControlError,ValueError) as exc:
        if any(marker in str(exc).lower() for marker in (
                'partial path','no usable bounded waypoint path',
                'path distance outside bounded travel limits',
                'unsupported vertical/off-mesh transition',
                'route has excessive waypoint count',
                'route starts away from the verified player position')):
            raise EncounterRouteUnavailable('NPC destination unavailable: '+str(exc)) from exc
        raise


def patrol_hazard_positions(awareness, state, measured):
    """Reuse live hostility and race-aware classification during a detour wait."""
    _, enemies, unknown = awareness.survey(state, measured, probe=False)
    return [m.position for m in (*enemies, *unknown)
            if abs(m.position.z-measured.z)<=8]


def bounded_position_jump(reason):
    """Only modest, explicitly measured forward jumps may be re-anchored once."""
    import re
    match = re.match(r'^Implausible world jump during forward movement: (\d+\.\d+)y in ', str(reason))
    return bool(match and 0 < float(match.group(1)) <= 40)


def confirm_stopped_position(sample, sleep, reason):
    state, measured = sample()
    if bounded_position_jump(reason):
        sleep(.25)
        state2, second = sample()
        if (state.player.dead or state.player.in_combat or state2.player.dead
                or state2.player.in_combat or measured.position.distance_to(second.position)>1.5):
            raise LiveControlError('Position did not stabilize after unexpected movement')
        return state2, second
    return state, measured


def _wait_for_patrol_route(*, driver, nav, map_id, dest, rejected, guard_retry,
                           supervise, sleep, log):
    """Refresh observed patrol positions while searching for a safe detour."""
    raw=None
    awareness=getattr(driver,'awareness',None)
    # Patrols are mobile. Wait briefly and refresh exact live
    # creature positions rather than blindly retry the route.
    if awareness is not None:
        log('No immediate safe detour; watching patrols for up to 12 seconds')
        for _ in range(6):
            guard_retry()
            for _tick in range(10):
                sleep(.2)
                state,measured=supervise()
            live=patrol_hazard_positions(awareness,state,measured)
            if not live:
                # Fresh stationary threat scan still runs before moving.
                raw=nav.find_path(map_id,measured.position,dest)
                if raw and abs(raw[0].z-measured.z)<=2.5:
                    raw=verified_npc_route(raw,measured.position,dest,map_id=map_id)
                    break
                raw=None
            else:
                raw=detour_around_encounters(nav,map_id,measured.position,dest,live,
                    rejected=rejected,guard=guard_retry,log=log)
                if raw:
                    break
    return raw


def visit_known_patrol(*,npc_name,npc_id,pid,capture,driver,nav,spawns,
                       world,owner,dest,sleep,now,world_position,travel,log):
    from wotlkbuddy.horde.npc_patrol import visit_patrolling_npc
    lookup=getattr(spawns,'patrol',None)
    points=lookup(npc_id,world.map_id,dest) if callable(lookup) else ()
    if not points:
        return None
    if getattr(driver,'awareness',None) is None:
        raise NpcTargetUnavailable(f'{npc_name}: verified server patrol requires live NPC awareness')
    log(f'{npc_name} ({npc_id}): moving NPC identified from server patrol ({len(points)} waypoints)')
    def walk(point,budget,watch=False):
        return visit_npc(npc_name=npc_name,npc_id=npc_id,pid=pid,capture=capture,
            driver=driver,nav=nav,spawns=spawns,sleep=sleep,now=now,
            world_position=world_position,travel=travel,log=log,
            _waypoint=point,_travel_budget=budget,_patrol_watch=watch)
    return visit_patrolling_npc(points=points,npc_name=npc_name,npc_id=npc_id,
        pid=pid,owner=owner,map_id=world.map_id,capture=capture,
        world_position=world_position,driver=driver,sleep=sleep,now=now,log=log,
        walk=walk,walk_search=lambda point,budget:walk(point,budget,True),
        walk_chase=lambda point,budget:walk(point,budget,'chase'))


def walk_npc_path(path,budget,*,travel,sample,driver,nav,map_id,log,npc_id,patrol_watch):
    scan=getattr(driver,'scan_hostiles',None)
    def patrol_scan(state,where,legs):
        from wotlkbuddy.horde.npc_patrol import nearby_npc,SIGHTING_REASON,patrol_intercept_required
        mob=nearby_npc(state,where,driver,npc_id)
        if patrol_intercept_required(mob,where,path[-1],path[0],patrol_watch):
            raise LiveControlError(SIGHTING_REASON)
        result=scan(state,where,legs) if callable(scan) else state
        # Combat, loot and drinking can consume enough time for the NPC to
        # leave the old destination. Read again before allowing further W.
        if patrol_watch=='chase':
            current,position=sample()
            if not current.player.in_combat and patrol_intercept_required(
                    nearby_npc(current,position,driver,npc_id),position,path[-1],path[0],patrol_watch):
                raise LiveControlError(SIGHTING_REASON)
        return result
    return travel(path,max_seconds=budget,sample=sample,
        keyboard=getattr(driver,'keyboard',driver),stop_requested=driver.emergency_stop_requested,
        expected_quest_id=None,expected_map_id=map_id,
        can_traverse=lambda a,b:nav.segment_clear(map_id,a,b),
        replan_path=lambda a,b:nav.find_path(map_id,a,b),
        destination_status=getattr(driver,'set_navigation_destination',None),
        distance_status=getattr(driver,'set_navigation_distance',None),
        progress=log,on_aggro=getattr(driver,'on_aggro',None),
        on_threat=patrol_scan if patrol_watch else scan,
        waypoint_spacing=18.,continuous_forward=True,
        fast_observe=getattr(driver,'fast_travel_observe',None),threat_check_yards=3. if patrol_watch=='chase' else 6.)


def visit_npc(
    *, npc_name: str, npc_id: int, pid: int, capture: Callable[[], ClientTelemetry],
    driver, nav, spawns, radius: float = 160,
    destination_hint=None,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    world_position: Callable[[ClientTelemetry], tuple[int, object]] | None = None,
    travel: Callable[..., object] = follow_world_path,
    log: Callable[[str], None] = lambda _: None,
    _waypoint=None, _travel_budget=450, _patrol_watch=False,
) -> ClientTelemetry:
    if not (npc_name and 0 < npc_id < 2**24 and 20 <= radius <= 600):
        raise ValueError('Invalid NPC identity/search radius')
    world_position = world_position or (
        lambda snap: verified_live_position(snap, pid=pid))
    first = capture()
    player = first.player
    if (first.truncated or not player.guid or player.dead or player.in_combat or
            first.resources is None or first.resources.class_token.upper() != 'MAGE'):
        raise LiveControlError('Need a living, noncombat Mage with verified telemetry')
    actual_pid, world = world_position(first)
    if actual_pid != pid:
        raise LiveControlError('Active WoW PID changed')
    if driver.emergency_stop_requested() or not driver.wow_is_foreground():
        raise LiveControlError('Escape pressed or WoW not foregrounded')
    # A quest/progress panel belongs to the *previous* NPC until closed.
    # Target changes alone do not change the client UI owner. Never walk to
    # another quest giver while that dialogue remains in the foreground.
    if first.dialog is not None and first.dialog.kind != 'none':
        first = dismiss_quest_dialog(
            capture=capture, driver=driver, expected_guid=player.guid,
            sleep=sleep, now=now, log=log)
        actual_pid, world = world_position(first)
        if actual_pid != pid:
            raise LiveControlError('Active WoW PID changed during UI cleanup')
    dest = _waypoint if _waypoint is not None else resolve_npc_spawn(
        spawns, world, npc_id, npc_name, radius, destination_hint)
    if _waypoint is None:
        found = visit_known_patrol(npc_name=npc_name,npc_id=npc_id,pid=pid,capture=capture,
            driver=driver,nav=nav,spawns=spawns,world=world,owner=player.guid,dest=dest,
            sleep=sleep,now=now,world_position=world_position,travel=travel,log=log)
        if found is not None:
            return found
    if not nav.ping():
        raise LiveControlError('Navmesh helper not responding')
    if hypot(world.x-dest.x, world.y-dest.y) > 3 or abs(world.z-dest.z) > 5:
        path = verified_npc_route(
            nav.find_path(world.map_id, world.position, dest),
            world.position, dest, map_id=world.map_id)
        log(f'Visiting {npc_name} ({npc_id}) via {len(path)-1} Detour legs')
        def sample():
            state = capture()
            if state.truncated or state.player.guid != player.guid:
                raise LiveControlError('Player identity changed during NPC visit')
            seen_pid, pos = world_position(state)
            if seen_pid != pid or pos.map_id!=world.map_id:
                raise LiveControlError('WoW PID or map changed during NPC visit')
            return state, pos
        def execute_visit():
            route=path
            deadline=now()+_travel_budget
            encounter_replans=0
            height_recovery=HeightRecovery(world.position,dest)
            rejected=[]
            # All planning and patrol waits are supervised. An attack
            # invalidates the route search; defend now and re-anchor later.
            from wotlkbuddy.horde.hunt_routes import RouteCombatInterrupted
            def supervise():
                if driver.emergency_stop_requested() or not driver.wow_is_foreground():
                    raise LiveControlError('Escape pressed or WoW lost focus during encounter detour')
                state,where=sample()
                if state.player.dead:
                    raise LiveControlError('Verified death during encounter detour')
                if state.player.in_combat:
                    raise RouteCombatInterrupted('Aggro/combat detected during encounter detour')
                return state,where
            def defend_and_replan():
                state,where=sample()
                if state.player.dead:
                    raise LiveControlError('Verified death during encounter detour')
                if state.player.in_combat:
                    handler=getattr(driver,'on_aggro',None)
                    if not callable(handler):
                        raise LiveControlError('Aggro/combat detected; defensive handler unavailable or route fight budget exhausted')
                    log('Aggro during NPC route planning/wait; immediately defending before replanning')
                    handler(state)
                    state,where=supervise()
                raw=nav.find_path(world.map_id,where.position,dest)
                if not raw or abs(raw[0].z-where.z)>2.5:
                    raise EncounterRouteUnavailable('NPC replan does not start on measured floor')
                return verified_npc_route(raw,where.position,dest,map_id=world.map_id)
            while True:
                remaining=deadline-now()
                if remaining<15:
                    raise EncounterRouteUnavailable(f'NPC visit to {npc_name} exhausted travel time budget')
                try:
                    supervise()
                except RouteCombatInterrupted:
                    route=defend_and_replan()
                    continue
                def walk(p,t):
                    return walk_npc_path(p,t,travel=travel,sample=sample,driver=driver,nav=nav,
                        map_id=world.map_id,log=log,npc_id=npc_id,patrol_watch=_patrol_watch)
                result=walk_with_elevator(
                    route, sample=sample, walk=walk,
                    nav=nav, destination=dest, map_id=world.map_id,
                    keyboard=getattr(driver,'keyboard',driver), pid=pid,
                    owner=player.guid, remaining=remaining, log=log, now=now)
                reason=result.stopped_reason
                if reason=='complete':
                    return result
                if _waypoint is not None:
                    from wotlkbuddy.horde.npc_patrol import PatrolSighting, PatrolTravelTimeout, SIGHTING_REASON
                    if reason == SIGHTING_REASON and _patrol_watch:
                        raise PatrolSighting(reason)
                    if reason == 'World navigation time limit reached':
                        raise PatrolTravelTimeout(reason)
                blocked=unsafe_route_encounter(reason)
                encounter=blocked or encounter_route_failure(reason) or (encounter_replans == 0 and bounded_position_jump(reason))
                if not encounter and not height_route_failure(reason):
                    raise LiveControlError(f'NPC visit to {npc_name} stopped: {reason}')
                state,measured=confirm_stopped_position(sample,sleep,reason)
                if (state.player.dead or driver.emergency_stop_requested() or not driver.wow_is_foreground()):
                    raise LiveControlError(f'Unsafe state after interrupted NPC route: {reason}')
                if state.player.in_combat:
                    route=defend_and_replan()
                    continue
                if encounter:
                    encounter_replans+=1
                    if encounter_replans>12:
                        raise EncounterRouteUnavailable(f'NPC visit to {npc_name} exhausted encounter replan budget: {reason}')
                    log(f'Encounter displacement recovery {encounter_replans}/12; retaining NPC {npc_name}')
                else:
                    if not height_recovery.record(measured.position):
                        raise EncounterRouteUnavailable(f'NPC visit to {npc_name} repeated height/floor mismatch without progress: {reason}')
                    log(f'Height recovery {height_recovery.attempts}: {height_recovery.best_remaining:.1f}y best remaining; {height_recovery.stagnant}/3 stagnant')
                if hypot(measured.x-dest.x,measured.y-dest.y)<=3 and abs(measured.z-dest.z)<=5:
                    return result  # final exact arrival and identity guard below
                log(f'Replanning NPC route from actual XYZ ({measured.x:.2f},{measured.y:.2f},{measured.z:.2f}) '
                    f'to {npc_name} ({dest.x:.2f},{dest.y:.2f},{dest.z:.2f})')
                if blocked:
                    if len(route)>1:
                        rejected.append(route[1])
                    def guard_retry():
                        if driver.emergency_stop_requested() or not driver.wow_is_foreground():
                            raise LiveControlError('Escape pressed or WoW lost focus during encounter detour')
                        supervise()
                        if now()>=deadline-15:
                            raise EncounterRouteUnavailable(f'NPC visit to {npc_name} exhausted detour time')
                    hazards=[p for p,t in getattr(driver,'dangerous_hunt_positions',[]) if monotonic()-t<90]
                    if not hazards:
                        raise EncounterRouteUnavailable(f'NPC visit to {npc_name}: blocking encounter has no verified positions')
                    try:
                        raw=detour_around_encounters(nav,world.map_id,measured.position,dest,hazards,
                                                    rejected=rejected,guard=guard_retry,log=log)
                        supervise()
                        if raw is None:
                            raw=_wait_for_patrol_route(
                                driver=driver,nav=nav,map_id=world.map_id,dest=dest,
                                rejected=rejected,guard_retry=guard_retry,
                                supervise=supervise,sleep=sleep,log=log)
                            supervise()
                            if raw is None:
                                raise EncounterRouteUnavailable(f'NPC visit to {npc_name}: no safe detour around observed pack after patrol wait')
                    except RouteCombatInterrupted:
                        route=defend_and_replan()
                        continue
                    route=raw
                    continue
                raw=nav.find_path(world.map_id,measured.position,dest)
                if not raw or abs(raw[0].z-measured.z)>2.5:
                    raise EncounterRouteUnavailable('NPC replan does not start on measured floor')
                route=verified_npc_route(raw,measured.position,dest,map_id=world.map_id)
        navigate_with_status(driver,'npc',execute_visit,target=npc_name)

    after = capture()
    after_pid, at = world_position(after)
    if (after_pid != pid or at.map_id!=world.map_id or after.truncated or after.player.guid != player.guid or
            after.player.dead or after.player.in_combat or
            hypot(at.x-dest.x, at.y-dest.y) > 7 or
            abs(at.z-dest.z) > 5):
        raise LiveControlError('NPC arrival/character state not confirmed')
    if _waypoint is not None:
        return after
    return target_npc(npc_name, npc_id, player.guid, capture, driver, sleep, now, log)


def target_npc(npc_name, npc_id, owner, capture, driver, sleep, now, log):
    log(f'Targeting verified {npc_name} (entry {npc_id})')
    driver.chat('/targetexact ' + npc_name)
    deadline = now() + 7
    while now() < deadline:
        sleep(.5)
        s = capture()
        if (s.truncated or s.player.guid != owner or s.player.dead or
                s.player.in_combat or driver.emergency_stop_requested() or
                not driver.wow_is_foreground()):
            raise LiveControlError('Unsafe player state while targeting NPC')
        if s.player.target_name == npc_name and \
                creature_entry_from_guid(s.player.target_guid) == npc_id:
            return s
    raise NpcTargetUnavailable(f'NPC {npc_name} entry {npc_id} not verified after targeting')
